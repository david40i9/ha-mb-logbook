"""WebSocket API used by the Mercedes EV Logbook card."""

from __future__ import annotations

import re
import time
from datetime import timedelta
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAPACITY,
    CONF_CAR_NAME,
    CONF_CHARGER_ENERGY,
    CONF_DISTANCE_UNIT,
    CONF_HOME_ZONE,
    CONF_ODOMETER,
    CONF_RANGE,
    CONF_SOC,
    DOMAIN,
    IDLE_MIN_HOURS,
    SIGNAL_UPDATED,
    UNIT_KM,
    VERSION,
    entry_conf,
)
from .db import distance_m
from .logger import _num, _to_km
from .temperature import temp_c, temp_source, to_c


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
    websocket_api.async_register_command(hass, ws_split_trip)
    websocket_api.async_register_command(hass, ws_merge_trips)
    websocket_api.async_register_command(hass, ws_temp_efficiency)
    websocket_api.async_register_command(hass, ws_suggest_names)
    websocket_api.async_register_command(hass, ws_trip_label)
    websocket_api.async_register_command(hass, ws_labels)
    websocket_api.async_register_command(hass, ws_place_labels)
    websocket_api.async_register_command(hass, ws_place_label_set)
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


PHOTON_URL = "https://photon.komoot.io/reverse"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Things worth naming a stop after: shops, cafés, car parks, chargers, stations...
POI_KEYS = "shop|amenity|tourism|leisure|office|craft|healthcare|railway|public_transport|club|sport|aeroway"
USER_AGENT = f"Mercedes-EV-Logbook/{VERSION} (Home Assistant integration)"
# UK-style postcodes (and bare outward codes) that sometimes come back as names.
POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?(\s*\d[A-Z]{2})?$", re.IGNORECASE)
LOOKUP_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def _photon(session, lat: float, lon: float, radius: float) -> list[dict[str, Any]]:
    """Photon (OpenStreetMap data, by Komoot): businesses and venues near the point."""
    base = [("lat", f"{lat:.6f}"), ("lon", f"{lon:.6f}"), ("radius", f"{radius / 1000:.2f}"), ("lang", "en")]
    pois = base + [("limit", "20")] + [("osm_tag", k) for k in POI_KEYS.split("|")]
    features = []
    for params in (pois,):
        async with session.get(
            PHOTON_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=LOOKUP_TIMEOUT
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientError(f"Photon said {resp.status}")
            features += (await resp.json(content_type=None)).get("features", [])
    out = []
    for f in features:
        props = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if not props.get("name") or len(coords) < 2:
            continue
        if props.get("osm_value") == "postcode" or props.get("type") == "postcode":
            continue  # postcodes aren't useful place names
        out.append(
            {"name": props["name"], "key": props.get("osm_key", ""), "kind": props.get("osm_value", ""),
             "lat": coords[1], "lon": coords[0]}
        )
    return out


async def _overpass(session, lat: float, lon: float, radius: float) -> list[dict[str, Any]]:
    """Overpass API fallback: named POIs within the radius."""
    query = (
        f"[out:json][timeout:10];"
        f'nwr(around:{radius:.0f},{lat:.6f},{lon:.6f})[name][~"^({POI_KEYS})$"~"."];'
        f"out center tags 60;"
    )
    async with session.post(
        OVERPASS_URL, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=LOOKUP_TIMEOUT
    ) as resp:
        if resp.status != 200:
            raise aiohttp.ClientError(f"Overpass said {resp.status}")
        data = await resp.json(content_type=None)
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        plat = el.get("lat", (el.get("center") or {}).get("lat"))
        plon = el.get("lon", (el.get("center") or {}).get("lon"))
        if not tags.get("name") or plat is None or plon is None:
            continue
        key = next((k for k in POI_KEYS.split("|") if k in tags), "")
        out.append({"name": tags["name"], "key": key, "kind": tags.get(key, ""), "lat": plat, "lon": plon})
    return out


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/suggest_names",
        vol.Required("lat"): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
        vol.Required("lon"): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
        vol.Optional("radius_m", default=150): vol.All(vol.Coerce(float), vol.Range(min=20, max=1000)),
    }
)
@websocket_api.async_response
async def ws_suggest_names(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Named businesses/places near a point, from OpenStreetMap data.

    Only runs when the user asks for suggestions, and sends nothing but this
    one point; no car, trip or account data. Photon first (fast), Overpass if
    Photon fails. Businesses come first, then nearby street names.
    """
    lat, lon = msg["lat"], msg["lon"]
    radius = max(80.0, msg["radius_m"])
    session = async_get_clientsession(hass)
    found: list[dict[str, Any]] | None = None
    errors = []
    for source in (_photon, _overpass):
        try:
            found = await source(session, lat, lon, radius)
            break
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            errors.append(str(err) or type(err).__name__)
    if found is None:
        connection.send_error(
            msg["id"], "lookup_failed", "OpenStreetMap lookups are busy, try again shortly (" + "; ".join(errors) + ")"
        )
        return

    poi_keys = set(POI_KEYS.split("|"))
    # Street furniture is named sometimes but never what you'd call a stop.
    skip = {"atm", "bench", "waste_basket", "bicycle_parking", "post_box", "telephone",
            "vending_machine", "recycling", "parcel_locker", "drinking_water"}
    best: dict[str, dict[str, Any]] = {}
    for f in found:
        if f["kind"] in skip:
            continue
        dist = distance_m(lat, lon, f["lat"], f["lon"])
        if dist > radius * 1.5:
            continue
        rank = 0 if f["key"] in poi_keys else 1  # businesses before streets/areas
        item = {"name": f["name"], "kind": str(f["kind"]).replace("_", " "), "distance_m": round(dist), "rank": rank}
        if f["name"] not in best or (rank, dist) < (best[f["name"]]["rank"], best[f["name"]]["distance_m"]):
            best[f["name"]] = item
    # Businesses and venues only: no streets, areas or postcodes.
    suggestions = sorted(
        (s for s in best.values() if s["rank"] == 0 and not POSTCODE_RE.match(s["name"])),
        key=lambda s: s["distance_m"],
    )[:10]
    connection.send_result(msg["id"], suggestions)


DEFAULT_LABELS = ["Business", "Personal", "Commute"]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/labels"})
@websocket_api.async_response
async def ws_labels(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Labels to offer: the defaults plus any custom ones already used."""
    db = hass.data[DOMAIN]["db"]
    used = await hass.async_add_executor_job(db.label_names)
    connection.send_result(msg["id"], DEFAULT_LABELS + [x for x in used if x not in DEFAULT_LABELS])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/trip_label",
        vol.Required("trip_id"): int,
        vol.Optional("label"): vol.Any(None, vol.All(str, vol.Strip, vol.Length(max=30))),
    }
)
@websocket_api.async_response
async def ws_trip_label(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Set (or clear, with an empty/None label) a trip's label."""
    db = hass.data[DOMAIN]["db"]
    ok = await hass.async_add_executor_job(db.set_trip_label, msg["trip_id"], msg.get("label") or None)
    if not ok:
        connection.send_error(msg["id"], "not_found", "No such trip")
        return
    connection.send_result(msg["id"], {"trip_id": msg["trip_id"], "label": msg.get("label") or None})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/place_labels"})
@websocket_api.async_response
async def ws_place_labels(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Places trips go to (with visits) and each one's default trip label."""
    db = hass.data[DOMAIN]["db"]
    visited = await hass.async_add_executor_job(db.visited_locations)
    defaults = await hass.async_add_executor_job(db.place_labels)
    seen = {v["location"] for v in visited}
    for loc in defaults:  # defaults for places with no trips in the log yet
        if loc not in seen:
            visited.append({"location": loc, "visits": 0})
    # Names of the cars' home zones: the card asks before giving these a default,
    # since nearly every trip starts or ends at home.
    homes = set()
    for entry in _entries(hass).values():
        st = hass.states.get(entry_conf(entry).get(CONF_HOME_ZONE, "zone.home"))
        if st is not None:
            homes.add(st.name)
    for v in visited:
        v["label"] = defaults.get(v["location"])
        v["is_home"] = v["location"] in homes
    connection.send_result(msg["id"], visited)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/place_label_set",
        vol.Required("location"): vol.All(str, vol.Strip, vol.Length(min=1, max=60)),
        vol.Optional("label"): vol.Any(None, vol.All(str, vol.Strip, vol.Length(max=30))),
    }
)
@websocket_api.async_response
async def ws_place_label_set(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    await hass.async_add_executor_job(db.set_place_label, msg["location"], msg.get("label") or None)
    connection.send_result(msg["id"], {"location": msg["location"], "label": msg.get("label") or None})


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
        vol.Required("rate"): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
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
    # Battery health: range the car predicts at 100%, from readings taken at a high
    # battery % (low ones exaggerate rounding). Median per month.
    projected: dict[str, list[float]] = {}
    for t in trips:
        mo = months.get(_month_key(t["start_ts"]))
        if mo is None:
            continue
        for soc, rng in ((t["soc_start"], t["range_start_km"]), (t["soc_end"], t["range_end_km"])):
            if soc is not None and rng and soc >= HEALTH_MIN_SOC:
                projected.setdefault(_month_key(t["start_ts"]), []).append(rng / soc * 100)
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
    for key, mo in months.items():
        vals = sorted(projected.get(key, []))
        mo["range100_km"] = vals[len(vals) // 2] if vals else None
        mo["range100_samples"] = len(vals)
    top = sorted(places.items(), key=lambda kv: -kv[1])[:8]
    connection.send_result(
        msg["id"],
        {
            "months": [{"month": k, **v} for k, v in months.items()],
            "top_places": [{"name": n, "count": c} for n, c in top],
        },
    )


HEALTH_MIN_SOC = 70
# Outside temperature bands (°C) for efficiency vs temperature.
TEMP_BANDS = [(-99, 0), (0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 99)]


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/temp_efficiency",
        vol.Required("entry_id"): str,
        vol.Optional("months", default=12): vol.All(int, vol.Range(min=1, max=60)),
    }
)
@websocket_api.async_response
async def ws_temp_efficiency(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Driving efficiency by outside temperature at each trip's start."""
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415
    from homeassistant.components.recorder.statistics import statistics_during_period  # noqa: PLC0415

    entry = _car(hass, msg["entry_id"])
    logger = entry.runtime_data
    conf = entry_conf(entry)
    source = temp_source(hass, conf)
    cap = float(conf.get(CONF_CAPACITY) or 0)
    st = hass.states.get(source) if source else None
    info = {"source": source, "name": st.name if st else source}
    if not source or not cap:
        connection.send_result(msg["id"], {**info, "bands": []})
        return
    start = _month_start(msg["months"] - 1)
    trips = await hass.async_add_executor_job(logger.db.trips, logger.car_id, start, time.time() + 86400, 100000)
    # Trips store the temperature when they start; fill older ones from the recorder
    # (a sensor's long-term hourly statistics, or a weather entity's state history,
    # which only goes back as far as the recorder keeps history) and save them.
    missing = [t for t in trips if t.get("temp_c") is None]
    if missing:
        first = dt_util.utc_from_timestamp(min(t["start_ts"] for t in missing) - 3 * 3600)
        found: list[tuple[int, float]] = []
        if source.startswith("weather."):
            hist = await get_instance(hass).async_add_executor_job(_history, hass, first, source)
            readings = [(s.last_updated.timestamp(), temp_c(s)) for s in hist]
            readings = [(ts, v) for ts, v in readings if v is not None]
            for t in missing:
                before = [v for ts, v in readings if t["start_ts"] - 3 * 3600 <= ts <= t["start_ts"]]
                if before:
                    found.append((t["id"], before[-1]))
        else:
            stats = await get_instance(hass).async_add_executor_job(
                statistics_during_period, hass, first, None, {source}, "hour", None, {"mean"},
            )
            unit = st.attributes.get("unit_of_measurement") if st else None
            hourly = {int(r["start"]) // 3600: r["mean"] for r in stats.get(source, []) if r.get("mean") is not None}
            for t in missing:
                v = to_c(hourly.get(int(t["start_ts"]) // 3600), unit)
                if v is not None:
                    found.append((t["id"], v))
        if found:
            await hass.async_add_executor_job(logger.db.set_trip_temps, found)
            temps = dict(found)
            for t in missing:
                t["temp_c"] = temps.get(t["id"])
    bands = [{"lo": lo, "hi": hi, "trips": 0, "km": 0.0, "kwh": 0.0} for lo, hi in TEMP_BANDS]
    for t in trips:
        temp = t.get("temp_c")
        # Short trips round the battery % too coarsely to say much.
        if temp is None or t["distance_km"] < 5 or t["soc_start"] is None or t["soc_end"] is None or t["soc_start"] <= t["soc_end"]:
            continue
        for b in bands:
            if b["lo"] <= temp < b["hi"]:
                b["trips"] += 1
                b["km"] += t["distance_km"]
                b["kwh"] += (t["soc_start"] - t["soc_end"]) / 100 * cap
                break
    connection.send_result(msg["id"], {**info, "bands": bands})


def _history(hass: HomeAssistant, start, entity_id: str) -> list:
    from homeassistant.components.recorder import history  # noqa: PLC0415

    found = history.get_significant_states(
        hass, start, None, [entity_id],
        include_start_time_state=True, significant_changes_only=False, no_attributes=False,
    )
    return found.get(entity_id, [])


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
        # The recorded stop the place is being named from: always kept as a match.
        vol.Optional("anchor_lat"): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
        vol.Optional("anchor_lon"): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
    }
)
@websocket_api.async_response
async def ws_place_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    db = hass.data[DOMAIN]["db"]
    place_id = msg.get("place_id")
    linked_existing = False
    if place_id is None:
        # Naming a stop with a name already in use links it to that place
        # instead of making a second place with the same name.
        existing = await hass.async_add_executor_job(db.places)
        same = next((p for p in existing if p["name"].casefold() == msg["name"].casefold()), None)
        if same is not None and "anchor_lat" in msg:
            place_id = same["id"]
            linked_existing = True
    if not linked_existing:
        place = {
            "id": place_id,
            "name": msg["name"],
            "lat": msg["lat"],
            "lon": msg["lon"],
            "radius_m": msg["radius_m"],
        }
        place_id = await hass.async_add_executor_job(db.save_place, place)
    if "anchor_lat" in msg and "anchor_lon" in msg:
        await hass.async_add_executor_job(db.add_anchor, place_id, msg["anchor_lat"], msg["anchor_lon"])
    _refresh_all(hass)
    connection.send_result(msg["id"], {"id": place_id, "linked_existing": linked_existing})


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


# A gap this long between route points with the car still is a park the car
# never reported (e.g. no signal underground, so the ignition stayed "on").
SPLIT_GAP_S = 20 * 60


def _states_at(hass: HomeAssistant, ts: float, ids: list[str]) -> dict[str, Any]:
    """Each entity's recorded state at a moment."""
    from homeassistant.components.recorder import history  # noqa: PLC0415

    when = dt_util.utc_from_timestamp(ts)
    found = history.get_significant_states(
        hass, when, when + timedelta(seconds=1), ids,
        include_start_time_state=True, significant_changes_only=False, no_attributes=False,
    )
    return {eid: rows[0] for eid, rows in found.items() if rows}


def _path_km(pts: list) -> float:
    return sum(
        distance_m(a[1], a[2], b[1], b[2]) for a, b in zip(pts, pts[1:])
        if None not in (a[1], a[2], b[1], b[2])
    ) / 1000


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/split_trip", vol.Required("trip_id"): int}
)
@websocket_api.async_response
async def ws_split_trip(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Split a trip at its longest stop: two drives the car reported as one."""
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415

    db = hass.data[DOMAIN]["db"]
    trip = await hass.async_add_executor_job(db.raw_trip, msg["trip_id"])
    if trip is None:
        connection.send_error(msg["id"], "not_found", "Trip not found")
        return
    pts = trip["points"]
    gaps = [(pts[i + 1][0] - pts[i][0], i) for i in range(len(pts) - 1)]
    gap, i = max(gaps, default=(0, 0))
    if gap < SPLIT_GAP_S:
        connection.send_error(msg["id"], "no_stop", "No stop of 20 minutes or more in this trip's route.")
        return
    logger = next(
        (e.runtime_data for e in _entries(hass).values() if e.runtime_data.car_id == trip["car"]), None
    )
    t1, t2 = pts[i][0], pts[i + 1][0]
    lat, lon = pts[i][1], pts[i][2]
    vals: dict[str, dict[str, float | None]] = {"t1": {}, "t2": {}}
    if logger is not None:
        conf = logger.conf
        keys = {k: conf.get(k) for k in (CONF_SOC, CONF_ODOMETER, CONF_RANGE) if conf.get(k)}
        for tag, ts in (("t1", t1), ("t2", t2)):
            states = await get_instance(hass).async_add_executor_job(
                _states_at, hass, ts, list(keys.values())
            )
            for k, eid in keys.items():
                v = _num(states.get(eid))
                vals[tag][k] = v if k == CONF_SOC else _to_km(v, states.get(eid))
    place = logger._place(lat, lon)[0] if logger is not None else None
    # Distance: shared by route length, unless the odometer says better.
    d1, d2 = _path_km(pts[: i + 1]), _path_km(pts[i + 1 :])
    total = trip["distance_km"] or 0.0
    km1 = total * d1 / (d1 + d2) if d1 + d2 else total / 2
    odo1 = vals["t1"].get(CONF_ODOMETER)
    if odo1 is not None and trip["odo_start_km"] is not None and 0 < odo1 - trip["odo_start_km"] < total:
        km1 = odo1 - trip["odo_start_km"]
    soc1, soc2 = vals["t1"].get(CONF_SOC), vals["t2"].get(CONF_SOC)
    leg1 = {
        **trip, "end_ts": t1, "odo_end_km": odo1, "distance_km": round(km1, 3),
        "soc_end": soc1, "range_end_km": vals["t1"].get(CONF_RANGE),
        "end_lat": lat, "end_lon": lon, "end_place": place, "points": pts[: i + 1],
    }
    leg2 = {
        **trip, "start_ts": t2, "odo_start_km": vals["t2"].get(CONF_ODOMETER) or odo1,
        "distance_km": round(total - km1, 3), "soc_start": soc2,
        "range_start_km": vals["t2"].get(CONF_RANGE), "start_lat": lat, "start_lon": lon,
        "start_place": place, "points": [[t2, lat, lon], *pts[i + 1 :]],
        "park_hours": round(gap / 3600, 2), "park_drain_hours": round(gap / 3600, 2),
        "park_soc_drop": soc1 - soc2 if soc1 is not None and soc2 is not None else None,
        "park_charged": False,
    }
    await hass.async_add_executor_job(db.replace_trip, msg["trip_id"], [leg1, leg2])
    _refresh_all(hass)
    connection.send_result(msg["id"], {"split_at": t1, "resumed": t2})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/merge_trips", vol.Required("trip_id"): int}
)
@websocket_api.async_response
async def ws_merge_trips(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Join a trip with the car's next one (a drive the logger cut in two)."""
    db = hass.data[DOMAIN]["db"]
    first = await hass.async_add_executor_job(db.raw_trip, msg["trip_id"])
    if first is None:
        connection.send_error(msg["id"], "not_found", "Trip not found")
        return
    nxt = await hass.async_add_executor_job(db.next_trip, first["car"], first["start_ts"])
    if nxt is None:
        connection.send_error(msg["id"], "no_next", "There is no later trip to merge with.")
        return
    merged = {
        **first,
        "end_ts": nxt["end_ts"],
        "odo_end_km": nxt["odo_end_km"],
        "distance_km": round((first["distance_km"] or 0) + (nxt["distance_km"] or 0), 3),
        "soc_end": nxt["soc_end"],
        "range_end_km": nxt["range_end_km"],
        "end_lat": nxt["end_lat"],
        "end_lon": nxt["end_lon"],
        "end_place": nxt["end_place"],
        "points": first["points"] + nxt["points"],
    }
    await hass.async_add_executor_job(db.replace_trips, [first["id"], nxt["id"]], [merged])
    _refresh_all(hass)
    connection.send_result(msg["id"], {"merged": [first["id"], nxt["id"]]})
