"""WebSocket API used by the MB Logbook card."""

from __future__ import annotations

import time
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_CAPACITY,
    CONF_CAR_NAME,
    CONF_CHARGER_ENERGY,
    CONF_DISTANCE_UNIT,
    DOMAIN,
    SIGNAL_UPDATED,
    UNIT_KM,
    entry_conf,
)


def _entries(hass: HomeAssistant) -> dict[str, Any]:
    return {
        e.entry_id: e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    }


def _car(hass: HomeAssistant, entry_id: str):
    entry = _entries(hass).get(entry_id)
    if entry is None:
        raise vol.Invalid("unknown car")
    return entry


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_cars)
    websocket_api.async_register_command(hass, ws_trips)
    websocket_api.async_register_command(hass, ws_trip_points)
    websocket_api.async_register_command(hass, ws_charges)
    websocket_api.async_register_command(hass, ws_delete_trip)
    websocket_api.async_register_command(hass, ws_places)
    websocket_api.async_register_command(hass, ws_place_save)
    websocket_api.async_register_command(hass, ws_place_delete)


@callback
def _refresh_all(hass: HomeAssistant) -> None:
    """Place names feed the last-trip/last-charge attributes of every car."""
    for entry in _entries(hass).values():
        async_dispatcher_send(hass, SIGNAL_UPDATED.format(entry.runtime_data.car_id))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/places"})
@websocket_api.async_response
async def ws_places(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    places = await hass.async_add_executor_job(db.places)
    usage = await hass.async_add_executor_job(db.place_usage)
    for p in places:
        p["uses"] = usage.get(p["id"], 0)
    connection.send_result(msg["id"], places)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/place_save",
        vol.Optional("place_id"): int,
        vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1, max=60)),
        vol.Required("lat"): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
        vol.Required("lon"): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
        vol.Required("radius_m"): vol.All(vol.Coerce(float), vol.Range(min=20, max=5000)),
    }
)
@websocket_api.async_response
async def ws_place_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    place = {
        "id": msg.get("place_id"),
        "name": msg["name"],
        "lat": msg["lat"],
        "lon": msg["lon"],
        "radius_m": msg["radius_m"],
    }
    place_id = await hass.async_add_executor_job(db.save_place, place)
    _refresh_all(hass)
    connection.send_result(msg["id"], {"id": place_id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/place_delete", vol.Required("place_id"): int}
)
@websocket_api.async_response
async def ws_place_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    await hass.async_add_executor_job(db.delete_place, msg["place_id"])
    _refresh_all(hass)
    connection.send_result(msg["id"], {"deleted": msg["place_id"]})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/cars"})
@callback
def ws_cars(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    cars = []
    for entry in _entries(hass).values():
        conf = entry_conf(entry)
        logger = entry.runtime_data
        cars.append(
            {
                "entry_id": entry.entry_id,
                "name": conf.get(CONF_CAR_NAME) or entry.title,
                "unit": conf.get(CONF_DISTANCE_UNIT, UNIT_KM),
                "capacity_kwh": conf.get(CONF_CAPACITY),
                "has_meter": bool(conf.get(CONF_CHARGER_ENERGY)),
                "trip_active": logger.trip_active,
                "charge_active": logger.charge_active,
            }
        )
    connection.send_result(msg["id"], sorted(cars, key=lambda c: c["name"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/trips",
        vol.Required("entry_id"): str,
        vol.Optional("start"): vol.Coerce(float),
        vol.Optional("end"): vol.Coerce(float),
    }
)
@websocket_api.async_response
async def ws_trips(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _car(hass, msg["entry_id"])
    logger = entry.runtime_data
    start = msg.get("start", time.time() - 30 * 86400)
    end = msg.get("end", time.time() + 86400)
    rows = await hass.async_add_executor_job(
        logger.db.trips, logger.car_id, start, end
    )
    connection.send_result(msg["id"], rows)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/trip_points", vol.Required("trip_id"): int}
)
@websocket_api.async_response
async def ws_trip_points(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    found = await hass.async_add_executor_job(db.trip_points, msg["trip_id"])
    if found is None:
        connection.send_error(msg["id"], "not_found", "No such trip")
        return
    connection.send_result(msg["id"], found[1])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/charges",
        vol.Required("entry_id"): str,
        vol.Optional("start"): vol.Coerce(float),
        vol.Optional("end"): vol.Coerce(float),
    }
)
@websocket_api.async_response
async def ws_charges(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _car(hass, msg["entry_id"])
    logger = entry.runtime_data
    start = msg.get("start", time.time() - 30 * 86400)
    end = msg.get("end", time.time() + 86400)
    rows = await hass.async_add_executor_job(
        logger.db.charges, logger.car_id, start, end
    )
    connection.send_result(msg["id"], rows)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/delete_trip", vol.Required("trip_id"): int}
)
@websocket_api.async_response
async def ws_delete_trip(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    await hass.async_add_executor_job(db.delete_trip, msg["trip_id"])
    connection.send_result(msg["id"], {"deleted": msg["trip_id"]})
