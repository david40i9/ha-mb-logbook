"""Rebuild trips and charges from Home Assistant's recorder history.

Replays the car's recorded states through the same rules the live logger
uses, for the period before the logbook was set up. Re-running replaces the
previously imported rows (everything before the cut-off) and never touches
rows the live logger wrote.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .const import (
    CHARGE_GAP_MINUTES,
    CONF_CAPACITY,
    CONF_CHARGE_POWER,
    CONF_CHARGER_ENERGY,
    CONF_CHARGING,
    CONF_HOME_ZONE,
    CONF_IGNITION,
    CONF_MIN_DISTANCE,
    CONF_ODOMETER,
    CONF_PRICE,
    CONF_RANGE,
    CONF_SOC,
    CONF_STOP_MINUTES,
    CONF_TRACKER,
    CONF_TRIP_DISTANCE,
    DEFAULT_HOME_ZONE,
    DEFAULT_MIN_DISTANCE_KM,
    DEFAULT_STOP_MINUTES,
    IGNITION_OFF,
    MAX_METER_STEP_KWH,
    UNKNOWN_STATES,
)
from . import rules
from .logger import MOVE_TRIGGER_M, STALE_MINUTES, CarLogger, _moved_m, _num, _to_km

_LOGGER = logging.getLogger(__name__)


def _fetch(hass: HomeAssistant, start: datetime, end: datetime, ids: list[str]):
    return history.get_significant_states(
        hass,
        start,
        end,
        ids,
        include_start_time_state=True,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=False,
    )


class _Replay:
    """The live logger's rules, driven by recorded states instead of events."""

    def __init__(self, logger: CarLogger) -> None:
        self.lg = logger
        self.conf = logger.conf
        self.cur: dict[str, State] = {}
        self.trip: dict[str, Any] | None = None
        self.charge: dict[str, Any] | None = None
        self.park: dict[str, Any] = {"ts": None, "soc": None, "charged": False}
        self.trips: list[dict[str, Any]] = []
        self.charges: list[dict[str, Any]] = []
        self.stop = float(self.conf.get(CONF_STOP_MINUTES, DEFAULT_STOP_MINUTES)) * 60
        self.min_km = float(self.conf.get(CONF_MIN_DISTANCE, DEFAULT_MIN_DISTANCE_KM))

    # value helpers from the replayed "current" states
    def _val(self, key: str) -> float | None:
        eid = self.conf.get(key)
        return _num(self.cur.get(eid)) if eid else None

    def _km(self, key: str) -> float | None:
        eid = self.conf.get(key)
        st = self.cur.get(eid) if eid else None
        return _to_km(_num(st), st)

    def _pos(self):
        st = self.cur.get(self.conf[CONF_TRACKER])
        if st is None:
            return None
        lat, lon = st.attributes.get("latitude"), st.attributes.get("longitude")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon), st.attributes.get("timestamp")

    def _place(self, lat, lon):
        return self.lg._place(lat, lon)  # HA zones as they are now

    # ----- trips -----
    def start_trip(self, ts: float, odo_start_km: float | None = None) -> None:
        pos = self._pos()
        lat, lon = (pos[0], pos[1]) if pos else (None, None)
        soc = self._val(CONF_SOC)
        self.trip = {
            "start_ts": ts,
            "odo_start_km": odo_start_km if odo_start_km is not None else self._km(CONF_ODOMETER),
            "counter_last": self._km(CONF_TRIP_DISTANCE),
            "counter_km": 0.0,
            "soc_start": soc,
            "range_start_km": self._km(CONF_RANGE),
            "start_lat": lat,
            "start_lon": lon,
            "start_place": self._place(lat, lon)[0],
            "points": [[round(ts), lat, lon]] if pos else [],
            "last_fix": pos[2] if pos else None,
            "moved_ts": ts,
            **rules.park_fields(self.park, ts, soc),
        }

    def end_trip(self) -> None:
        t = self.trip
        self.trip = None
        odo_end = self._km(CONF_ODOMETER)
        odo_km = (
            odo_end - t["odo_start_km"]
            if odo_end is not None and t["odo_start_km"] is not None
            else None
        )
        counter = t["counter_km"] if self.conf.get(CONF_TRIP_DISTANCE) else None
        if counter is not None and (odo_km is None or abs(counter - odo_km) <= 2.0):
            distance = counter
        else:
            distance = odo_km or 0.0
        if distance < self.min_km:
            return
        pos = self._pos()
        lat, lon = (pos[0], pos[1]) if pos else (None, None)
        pts = t["points"]
        if pos and (not pts or pts[-1][1:] != [lat, lon]):
            pts.append([round(t["off_ts"]), lat, lon])
        soc_end = rules.trip_soc_end(self._val(CONF_SOC), self.charge, t["off_ts"])
        self.trips.append(
            {
                "car": self.lg.car_id,
                "start_ts": t["start_ts"],
                "end_ts": t["off_ts"],
                "odo_start_km": t["odo_start_km"],
                "odo_end_km": odo_end,
                "distance_km": round(distance, 3),
                "soc_start": t["soc_start"],
                "soc_end": soc_end,
                "range_start_km": t["range_start_km"],
                "range_end_km": self._km(CONF_RANGE),
                "start_lat": t["start_lat"],
                "start_lon": t["start_lon"],
                "end_lat": lat,
                "end_lon": lon,
                "start_place": t["start_place"],
                "end_place": self._place(lat, lon)[0],
                "park_hours": t["park_hours"],
                "park_drain_hours": t["park_drain_hours"],
                "park_soc_drop": t["park_soc_drop"],
                "park_charged": t["park_charged"],
                "points": pts,
            }
        )
        # A charge that started before the trip was closed belongs to this park.
        self.park = rules.new_park(
            t["off_ts"], soc_end, rules.charging_into_park(self.charge, t["off_ts"])
        )

    # ----- charges -----
    def start_charge(self, ts: float) -> None:
        pos = self._pos()
        lat, lon = (pos[0], pos[1]) if pos else (None, None)
        name, zone_id = self._place(lat, lon)
        self.charge = {
            "start_ts": ts,
            "soc_start": self._val(CONF_SOC),
            "place": name,
            "lat": lat,
            "lon": lon,
            "at_home": zone_id == self.conf.get(CONF_HOME_ZONE, DEFAULT_HOME_ZONE),
            "kwh_meter": 0.0,
            "cost": 0.0,
            "max_power_kw": 0.0,
            "last_ts": ts,
        }
        if self.conf.get(CONF_CHARGE_POWER):
            rules.add_power_sample(self.charge, ts, self._val(CONF_CHARGE_POWER) or 0.0, self._val(CONF_PRICE))
        self.park["charged"] = True

    def end_charge(self) -> None:
        c = self.charge
        self.charge = None
        soc_end = self._val(CONF_SOC)
        cap = float(self.conf.get(CONF_CAPACITY) or 0)
        kwh_car = (
            round((soc_end - c["soc_start"]) / 100 * cap, 2)
            if soc_end is not None and c["soc_start"] is not None and cap
            else None
        )
        meter = c["kwh_meter"]
        if meter <= 0 and (kwh_car is None or kwh_car <= 0):
            return
        rules.add_power_sample(c, c.get("off_ts", c["last_ts"]), 0.0, None)
        cost, basis = rules.charge_cost(c, bool(self.conf.get(CONF_PRICE)))
        self.charges.append(
            {
                "car": self.lg.car_id,
                "start_ts": c["start_ts"],
                "end_ts": c.get("off_ts", c["last_ts"]),  # when charging really stopped
                "soc_start": c["soc_start"],
                "soc_end": soc_end,
                "kwh_car": kwh_car,
                "kwh_meter": round(meter, 3) if meter > 0 else None,
                "cost": cost,
                "cost_basis": basis,
                "max_power_kw": c["max_power_kw"] or None,
                "place": c["place"],
                "at_home": c["at_home"],
                "lat": c["lat"],
                "lon": c["lon"],
            }
        )
        # Same park, now charged; drain is measured from the end of the charge.
        self.park = rules.after_charge(self.park, c.get("off_ts", c["last_ts"]), soc_end)

    # ----- driver -----
    def feed(self, ts: float, eid: str, st: State) -> None:
        if self.trip and "off_ts" in self.trip and ts >= self.trip["off_ts"] + self.stop:
            self.end_trip()
        if self.charge and "off_ts" in self.charge and ts >= self.charge["off_ts"] + CHARGE_GAP_MINUTES * 60:
            self.end_charge()
        c = self.conf
        if (
            c.get(CONF_IGNITION) and self.trip and "off_ts" not in self.trip
            and ts - self.trip["moved_ts"] >= STALE_MINUTES * 60
        ):
            # Ignition stuck on with the car still (lost signal): the live stale check.
            self.trip["off_ts"] = self.trip["moved_ts"]
            self.end_trip()
        old = self.cur.get(eid)
        self.cur[eid] = st
        if c.get(CONF_IGNITION) and (
            (eid == c[CONF_TRACKER] and _moved_m(old, st) >= MOVE_TRIGGER_M)
            or (eid == c.get(CONF_TRIP_DISTANCE) and (_num(st) or 0) > (_num(old) or 0))
        ):
            ign = self.cur.get(c[CONF_IGNITION])
            if self.trip is None and ign is not None and ign.state not in IGNITION_OFF and ign.state not in UNKNOWN_STATES:
                self.start_trip(ts)
            if self.trip is not None:
                self.trip["moved_ts"] = ts
        if not c.get(CONF_IGNITION):
            # No ignition sensor: the same movement rules as the live logger.
            moved, odo_before = False, None
            if eid == c[CONF_TRACKER] and _moved_m(old, st) >= MOVE_TRIGGER_M:
                moved = True
            elif eid == c.get(CONF_TRIP_DISTANCE) and (_num(st) or 0) > (_num(old) or 0):
                moved = True
            elif eid == c[CONF_ODOMETER]:
                b, a = _num(old), _num(st)
                if b is not None and a is not None and a > b:
                    moved, odo_before = True, _to_km(b, old)
            if moved:
                if self.trip is None:
                    self.start_trip(ts, odo_before)
                self.trip["off_ts"] = ts
        if eid == c.get(CONF_IGNITION):
            if st.state in UNKNOWN_STATES:
                return
            if st.state not in IGNITION_OFF:
                if self.trip is None:
                    self.start_trip(ts)
                else:
                    self.trip.pop("off_ts", None)
            elif self.trip is not None and "off_ts" not in self.trip:
                self.trip["off_ts"] = ts
        elif eid == c[CONF_TRACKER]:
            if self.trip is None:
                return
            pos = self._pos()
            if pos is None or (pos[2] is not None and pos[2] == self.trip["last_fix"]):
                return
            pts = self.trip["points"]
            if not pts or pts[-1][1:] != [pos[0], pos[1]]:
                pts.append([round(ts), pos[0], pos[1]])
                self.trip["last_fix"] = pos[2]
                self.trip["moved_ts"] = ts
        elif eid == c.get(CONF_TRIP_DISTANCE):
            if self.trip is None:
                return
            value = self._km(CONF_TRIP_DISTANCE)
            if value is None:
                return
            last = self.trip["counter_last"]
            if last is not None:
                self.trip["counter_km"] += value - last if value >= last else value
            self.trip["counter_last"] = value
        elif eid == c.get(CONF_CHARGING):
            if st.state in UNKNOWN_STATES:
                return
            if st.state == "on":
                if self.charge is None:
                    self.start_charge(ts)
                else:
                    self.charge.pop("off_ts", None)
            elif self.charge is not None and "off_ts" not in self.charge:
                self.charge["off_ts"] = ts
                rules.add_power_sample(self.charge, ts, 0.0, self._val(CONF_PRICE))
        elif eid == c.get(CONF_PRICE):
            if self.charge is not None and "off_ts" not in self.charge and self.charge.get("pw_last"):
                rules.add_power_sample(self.charge, ts, None, _num(st))
        elif eid == c.get(CONF_CHARGER_ENERGY):
            if self.charge is None or not self.charge["at_home"] or "off_ts" in self.charge:
                return
            o, n = _num(old), _num(st)
            if o is None or n is None or not 0 < n - o <= MAX_METER_STEP_KWH:
                return
            self.charge["kwh_meter"] += n - o
            price = self._val(CONF_PRICE)
            if price is not None:
                self.charge["cost"] += (n - o) * price
            self.charge["last_ts"] = ts
        elif eid == c.get(CONF_CHARGE_POWER):
            if self.charge is not None:
                p = _num(st)
                if p is not None and p > self.charge["max_power_kw"]:
                    self.charge["max_power_kw"] = p
                if p and p > 0:
                    self.charge["last_ts"] = ts
                if p is not None and "off_ts" not in self.charge:
                    rules.add_power_sample(self.charge, ts, p, self._val(CONF_PRICE))


async def async_import_history(
    hass: HomeAssistant, logger: CarLogger, cutoff: datetime, days: int = 365
) -> dict[str, int]:
    """Replay recorder history before ``cutoff`` and store it. Returns counts."""
    conf = logger.conf
    keys = (
        CONF_IGNITION, CONF_TRACKER, CONF_ODOMETER, CONF_SOC, CONF_RANGE,
        CONF_TRIP_DISTANCE, CONF_CHARGING, CONF_CHARGER_ENERGY, CONF_PRICE,
        CONF_CHARGE_POWER,
    )
    ids = [conf[k] for k in keys if conf.get(k)]
    start = cutoff - timedelta(days=days)
    states = await get_instance(hass).async_add_executor_job(_fetch, hass, start, cutoff, ids)

    events: list[tuple[float, int, str, State]] = []
    # Priority keeps simultaneous updates in a sensible order: values first,
    # then ignition/charging, so a trip starts with the values of that moment.
    prio = {conf[k]: 0 for k in keys if conf.get(k)}
    if conf.get(CONF_IGNITION):
        prio[conf[CONF_IGNITION]] = 1
    if conf.get(CONF_CHARGING):
        prio[conf[CONF_CHARGING]] = 1
    for eid, rows in states.items():
        for st in rows:
            events.append((st.last_updated.timestamp(), prio.get(eid, 0), eid, st))
    events.sort(key=lambda e: (e[0], e[1]))

    replay = _Replay(logger)
    for ts, _, eid, st in events:
        replay.feed(ts, eid, st)
    # Close anything that had finished before the cut-off.
    end_ts = cutoff.timestamp()
    if replay.trip and "off_ts" in replay.trip and end_ts >= replay.trip["off_ts"] + replay.stop:
        replay.end_trip()
    if replay.charge and "off_ts" in replay.charge:
        replay.end_charge()

    db = logger.db
    await hass.async_add_executor_job(
        db.replace_before, logger.car_id, end_ts, replay.trips, replay.charges
    )
    logger.adopt_imported_park(replay.park, end_ts)
    _LOGGER.info(
        "%s: imported %d trips and %d charges from history (%s to %s)",
        logger.car_id, len(replay.trips), len(replay.charges),
        dt_util.as_local(start).date(), dt_util.as_local(cutoff).date(),
    )
    return {"trips": len(replay.trips), "charges": len(replay.charges)}
