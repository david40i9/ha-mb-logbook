"""WebSocket API used by the MB Logbook card."""

from __future__ import annotations

import time
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAPACITY,
    CONF_CAR_NAME,
    CONF_CHARGER_ENERGY,
    CONF_DISTANCE_UNIT,
    DOMAIN,
    IDLE_MIN_HOURS,
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
    websocket_api.async_register_command(hass, ws_rates)
    websocket_api.async_register_command(hass, ws_rate_save)
    websocket_api.async_register_command(hass, ws_rate_delete)
    websocket_api.async_register_command(hass, ws_stats)
    websocket_api.async_register_command(hass, ws_routes)
    websocket_api.async_register_command(hass, ws_places)
    websocket_api.async_register_command(hass, ws_place_save)
    websocket_api.async_register_command(hass, ws_place_delete)


@callback
def _refresh_all(hass: HomeAssistant) -> None:
    """Place names feed the last-trip/last-charge attributes of every car."""
    for entry in _entries(hass).values():
        async_dispatcher_send(hass, SIGNAL_UPDATED.format(entry.runtime_data.car_id))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/rates"})
@websocket_api.async_response
async def ws_rates(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Charging locations seen so far, plus every rate entry."""
    db = hass.data[DOMAIN]["db"]
    rates = await hass.async_add_executor_job(db.rates)
    locations = await hass.async_add_executor_job(db.charge_locations)
    known = {loc["location"] for loc in locations}
    for r in rates:  # rates for places with no charges yet still need a row
        if r["location"] not in known:
            locations.append({"location": r["location"], "charges": 0, "metered": 0, "last_ts": 0})
            known.add(r["location"])
    connection.send_result(msg["id"], {"locations": locations, "rates": rates})


def _local_midnight(date_str: str) -> float:
    day = dt_util.parse_date(date_str)
    if day is None:
        raise vol.Invalid("bad date")
    return dt_util.start_of_local_day(day).timestamp()


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/rate_save",
        vol.Optional("rate_id"): int,
        vol.Required("location"): vol.All(str, vol.Strip, vol.Length(min=1, max=60)),
        vol.Required("valid_from"): str,  # YYYY-MM-DD, local
        vol.Required("rate"): vol.All(vol.Coerce(float), vol.Range(min=0, max=10)),
    }
)
@websocket_api.async_response
async def ws_rate_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    try:
        valid_from = _local_midnight(msg["valid_from"])
    except vol.Invalid:
        connection.send_error(msg["id"], "invalid_format", "Use a date like 2026-10-22")
        return
    rate_id = await hass.async_add_executor_job(
        db.save_rate,
        {"id": msg.get("rate_id"), "location": msg["location"], "valid_from": valid_from, "rate": msg["rate"]},
    )
    _refresh_all(hass)
    connection.send_result(msg["id"], {"id": rate_id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/rate_delete", vol.Required("rate_id"): int}
)
@websocket_api.async_response
async def ws_rate_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    await hass.async_add_executor_job(db.delete_rate, msg["rate_id"])
    _refresh_all(hass)
    connection.send_result(msg["id"], {"deleted": msg["rate_id"]})


def _month_key(ts: float) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%Y-%m")


def _month_start(months_back: int) -> float:
    """Local midnight on the 1st, ``months_back`` months before this month."""
    now = dt_util.now()
    y, m = now.year, now.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return now.replace(year=y, month=m, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/stats",
        vol.Required("entry_id"): str,
        vol.Optional("months", default=12): vol.All(int, vol.Range(min=1, max=60)),
    }
)
@websocket_api.async_response
async def ws_stats(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Per-month totals and most-visited places for the overview page."""
    entry = _car(hass, msg["entry_id"])
    logger = entry.runtime_data
    cap = float(entry_conf(entry).get(CONF_CAPACITY) or 0)
    start = _month_start(msg["months"] - 1)
    end = time.time() + 86400
    trips = await hass.async_add_executor_job(logger.db.trips, logger.car_id, start, end, 100000)
    charges = await hass.async_add_executor_job(logger.db.charges, logger.car_id, start, end, 100000)

    months: dict[str, dict[str, float]] = {}
    for i in range(msg["months"] - 1, -1, -1):
        months[_month_key(_month_start(i))] = {
            "trips": 0, "distance_km": 0.0, "kwh_used": 0.0, "km_with_kwh": 0.0,
            "charges": 0, "charged_kwh": 0.0, "cost": 0.0,
            "idle_hours": 0.0, "idle_drop": 0.0,
        }
    places: dict[str, int] = {}
    for t in trips:
        mo = months.get(_month_key(t["start_ts"]))
        if mo is None:
            continue
        mo["trips"] += 1
        mo["distance_km"] += t["distance_km"]
        if cap and t["soc_start"] is not None and t["soc_end"] is not None and t["soc_start"] > t["soc_end"]:
            mo["kwh_used"] += (t["soc_start"] - t["soc_end"]) / 100 * cap
            mo["km_with_kwh"] += t["distance_km"]
        hours = t.get("park_drain_hours")
        if hours is None and not t["park_charged"]:
            hours = t.get("park_hours")
        if t["park_soc_drop"] is not None and hours and hours >= IDLE_MIN_HOURS:
            mo["idle_hours"] += hours
            mo["idle_drop"] += t["park_soc_drop"]
        if t.get("end_place"):
            places[t["end_place"]] = places.get(t["end_place"], 0) + 1
    for c in charges:
        mo = months.get(_month_key(c["start_ts"]))
        if mo is None:
            continue
        mo["charges"] += 1
        mo["charged_kwh"] += c.get("kwh_meter") or c.get("kwh_car") or 0
        mo["cost"] += c.get("cost") or 0
    top = sorted(places.items(), key=lambda kv: -kv[1])[:8]
    connection.send_result(
        msg["id"],
        {
            "months": [{"month": k, **v} for k, v in months.items()],
            "top_places": [{"name": n, "count": c} for n, c in top],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/routes",
        vol.Required("entry_id"): str,
        vol.Required("start"): vol.Coerce(float),
        vol.Optional("end"): vol.Coerce(float),
    }
)
@websocket_api.async_response
async def ws_routes(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    entry = _car(hass, msg["entry_id"])
    logger = entry.runtime_data
    rows = await hass.async_add_executor_job(
        logger.db.routes, logger.car_id, msg["start"], msg.get("end", time.time() + 86400)
    )
    connection.send_result(msg["id"], rows)


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
    _refresh_all(hass)  # month totals and last-trip sensors
    connection.send_result(msg["id"], {"deleted": msg["trip_id"]})
