"""Per-car trip and charging-session detection.

Trips: a trip starts when the ignition leaves "off" and ends once it has been
off for ``stop_minutes``. Distance comes from the odometer (the car's own
"since start" counters only reset after a long park, so they can't split
trips). Short blips that don't move the odometer are dropped and the car is
treated as still parked.

Parks: the gap before each trip. If the car didn't charge in that gap, the
battery % lost is the idle drain.

Charging: follows the car's "charging active" sensor. If a charger energy
meter is configured and the car is in the home zone, the meter's increase
(and its cost at the live price) is credited to this car's session.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components.zone import async_active_zone
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store

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
    DOMAIN,
    IGNITION_OFF,
    KM_PER_MI,
    MAX_METER_STEP_KWH,
    SIGNAL_UPDATED,
    STORAGE_VERSION,
    UNKNOWN_STATES,
)
from .db import LogbookDB

_LOGGER = logging.getLogger(__name__)


def _num(state: State | None) -> float | None:
    if state is None or state.state in UNKNOWN_STATES:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _to_km(value: float | None, state: State | None) -> float | None:
    if value is None or state is None:
        return None
    unit = (state.attributes.get("unit_of_measurement") or "km").lower()
    return value * KM_PER_MI if unit in ("mi", "miles") else value


class CarLogger:
    """Watches one car's entities and writes trips and charges to the DB."""

    def __init__(
        self, hass: HomeAssistant, car_id: str, conf: dict[str, Any], db: LogbookDB
    ) -> None:
        self.hass = hass
        self.car_id = car_id
        self.conf = conf
        self.db = db
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{car_id}")
        self._state: dict[str, Any] = {}
        self._unsubs: list = []
        self._end_timer = None
        self._charge_end_timer = None

    # ----- public state used by entities -------------------------------------------

    @property
    def trip_active(self) -> bool:
        return self._state.get("trip") is not None

    @property
    def charge_active(self) -> bool:
        return self._state.get("charge") is not None

    @property
    def current_trip(self) -> dict[str, Any] | None:
        return self._state.get("trip")

    # ----- lifecycle ----------------------------------------------------------------

    async def async_start(self) -> None:
        self._state = await self._store.async_load() or {}
        watched = [
            self.conf[CONF_IGNITION],
            self.conf[CONF_TRACKER],
        ]
        if self.conf.get(CONF_CHARGING):
            watched.append(self.conf[CONF_CHARGING])
        if self.conf.get(CONF_TRIP_DISTANCE):
            watched.append(self.conf[CONF_TRIP_DISTANCE])
        self._unsubs.append(
            async_track_state_change_event(self.hass, watched, self._on_change)
        )
        if self.conf.get(CONF_CHARGER_ENERGY):
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, [self.conf[CONF_CHARGER_ENERGY]], self._on_meter
                )
            )
        if self.conf.get(CONF_CHARGE_POWER):
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, [self.conf[CONF_CHARGE_POWER]], self._on_power
                )
            )
        self._resync()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for timer in (self._end_timer, self._charge_end_timer):
            if timer:
                timer()
        await self._store.async_save(self._state)

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: self._state, 5)

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATED.format(self.car_id))

    @callback
    def _resync(self) -> None:
        """Bring saved state in line with the car after a restart."""
        ign = self.hass.states.get(self.conf[CONF_IGNITION])
        if self._state.get("trip") and ign is not None and ign.state in IGNITION_OFF:
            # Ended while HA was down: close it now using the last known values.
            self._state["trip"].setdefault("off_ts", time.time())
            self._schedule_trip_end(0)
        elif not self._state.get("trip") and self._ignition_on(ign):
            self._start_trip()
        if self.conf.get(CONF_CHARGING):
            chg = self.hass.states.get(self.conf[CONF_CHARGING])
            on = chg is not None and chg.state == "on"
            if on and not self._state.get("charge"):
                self._start_charge()
            elif not on and self._state.get("charge"):
                self._schedule_charge_end(0)
        if "park" not in self._state:
            self._state["park"] = self._park_snapshot()
        self._save()

    # ----- helpers --------------------------------------------------------------------

    def _ignition_on(self, state: State | None) -> bool:
        return (
            state is not None
            and state.state not in UNKNOWN_STATES
            and state.state not in IGNITION_OFF
        )

    def _odo_km(self) -> float | None:
        st = self.hass.states.get(self.conf[CONF_ODOMETER])
        return _to_km(_num(st), st)

    def _counter_km(self) -> float | None:
        """The car's 'distance since start' counter (0.1 resolution)."""
        if not self.conf.get(CONF_TRIP_DISTANCE):
            return None
        st = self.hass.states.get(self.conf[CONF_TRIP_DISTANCE])
        return _to_km(_num(st), st)

    def _soc(self) -> float | None:
        return _num(self.hass.states.get(self.conf[CONF_SOC]))

    def _range_km(self) -> float | None:
        if not self.conf.get(CONF_RANGE):
            return None
        st = self.hass.states.get(self.conf[CONF_RANGE])
        return _to_km(_num(st), st)

    def _position(self) -> tuple[float, float, str | None] | None:
        st = self.hass.states.get(self.conf[CONF_TRACKER])
        if st is None:
            return None
        lat = st.attributes.get(ATTR_LATITUDE)
        lon = st.attributes.get(ATTR_LONGITUDE)
        if lat is None or lon is None:
            return None
        return float(lat), float(lon), st.attributes.get("timestamp")

    def _place(self, lat: float | None, lon: float | None) -> tuple[str | None, str | None]:
        """(zone name, zone entity_id) for a position, from HA's own zones."""
        if lat is None or lon is None:
            return None, None
        zone = async_active_zone(self.hass, lat, lon, 0)
        if zone is None:
            return None, None
        return zone.name, zone.entity_id

    def _at_home(self) -> bool:
        pos = self._position()
        if pos is None:
            return False
        _, zone_id = self._place(pos[0], pos[1])
        return zone_id == self.conf.get(CONF_HOME_ZONE, DEFAULT_HOME_ZONE)

    def _park_snapshot(self) -> dict[str, Any]:
        return {"ts": time.time(), "soc": self._soc(), "charged": False}

    # ----- trips ----------------------------------------------------------------------

    @callback
    def _on_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new = event.data["new_state"]
        if entity_id == self.conf[CONF_IGNITION]:
            self._on_ignition(new)
        elif entity_id == self.conf[CONF_TRACKER]:
            self._on_position()
        elif entity_id == self.conf.get(CONF_CHARGING):
            self._on_charging(new)
        elif entity_id == self.conf.get(CONF_TRIP_DISTANCE):
            self._on_counter()

    @callback
    def _on_counter(self) -> None:
        """Accumulate the trip counter; it resets to 0 after a long park."""
        trip = self._state.get("trip")
        value = self._counter_km()
        if trip is None or value is None:
            return
        last = trip.get("counter_last")
        if last is not None:
            step = value - last if value >= last else value  # reset: count from 0
            trip["counter_km"] = (trip.get("counter_km") or 0.0) + step
        trip["counter_last"] = value
        self._save()

    @callback
    def _on_ignition(self, new: State | None) -> None:
        if new is None or new.state in UNKNOWN_STATES:
            return  # connectivity blips say nothing about driving
        trip = self._state.get("trip")
        if self._ignition_on(new):
            if self._end_timer:
                self._end_timer()
                self._end_timer = None
            if trip is None:
                self._start_trip()
            else:
                trip.pop("off_ts", None)
                self._save()
        elif trip is not None and "off_ts" not in trip:
            trip["off_ts"] = time.time()
            self._save()
            stop = self.conf.get(CONF_STOP_MINUTES, DEFAULT_STOP_MINUTES)
            self._schedule_trip_end(stop * 60)

    @callback
    def _schedule_trip_end(self, delay: float) -> None:
        if self._end_timer:
            self._end_timer()

        @callback
        def _fire(_now) -> None:
            self._end_timer = None
            self.hass.async_create_task(self._async_end_trip())

        self._end_timer = async_call_later(self.hass, timedelta(seconds=delay), _fire)

    @callback
    def _start_trip(self) -> None:
        pos = self._position()
        lat, lon = (pos[0], pos[1]) if pos else (None, None)
        place, _ = self._place(lat, lon)
        park = self._state.get("park") or {}
        now = time.time()
        soc = self._soc()
        park_soc = park.get("soc")
        self._state["trip"] = {
            "start_ts": now,
            "odo_start_km": self._odo_km(),
            "counter_last": self._counter_km(),
            "counter_km": 0.0,
            "soc_start": soc,
            "range_start_km": self._range_km(),
            "start_lat": lat,
            "start_lon": lon,
            "start_place": place,
            "points": [[round(now), lat, lon]] if pos else [],
            "last_fix": pos[2] if pos else None,
            "park_hours": round((now - park["ts"]) / 3600, 2) if park.get("ts") else None,
            "park_soc_drop": (
                round(park_soc - soc, 1) if park_soc is not None and soc is not None else None
            ),
            "park_charged": bool(park.get("charged")),
        }
        self._save()
        self._notify()

    @callback
    def _on_position(self) -> None:
        trip = self._state.get("trip")
        pos = self._position()
        if trip is None or pos is None:
            return
        lat, lon, fix = pos
        if fix is not None and fix == trip.get("last_fix"):
            return  # the car re-sent the same fix
        pts = trip["points"]
        if pts and pts[-1][1] == lat and pts[-1][2] == lon:
            return
        pts.append([round(time.time()), lat, lon])
        trip["last_fix"] = fix
        self._save()

    async def _async_end_trip(self) -> None:
        trip = self._state.get("trip")
        if trip is None:
            return
        odo_end = self._odo_km()
        odo_start = trip.get("odo_start_km")
        odo_km = odo_end - odo_start if odo_end is not None and odo_start is not None else None
        counter = trip.get("counter_km") if self.conf.get(CONF_TRIP_DISTANCE) else None
        # The counter has 0.1 resolution; the odometer may only report whole miles.
        # Trust the counter unless it disagrees with the odometer by more than that
        # rounding (e.g. a counter update was missed while HA was down).
        if counter is not None and (odo_km is None or abs(counter - odo_km) <= 2.0):
            distance = counter
        else:
            distance = odo_km or 0.0
        min_km = self.conf.get(CONF_MIN_DISTANCE, DEFAULT_MIN_DISTANCE_KM)
        self._state["trip"] = None
        if distance < min_km:
            # Ignition without driving (pre-climate, sitting in the car): still parked.
            _LOGGER.debug("%s: dropped %.2f km trip", self.car_id, distance)
            self._save()
            self._notify()
            return

        pos = self._position()
        lat, lon = (pos[0], pos[1]) if pos else (None, None)
        place, _ = self._place(lat, lon)
        points = trip.get("points") or []
        if pos and (not points or points[-1][1:] != [lat, lon]):
            points.append([round(trip.get("off_ts") or time.time()), lat, lon])
        soc_end = self._soc()
        record = {
            "car": self.car_id,
            "start_ts": trip["start_ts"],
            "end_ts": trip.get("off_ts") or time.time(),
            "odo_start_km": odo_start,
            "odo_end_km": odo_end,
            "distance_km": round(distance, 3),
            "soc_start": trip.get("soc_start"),
            "soc_end": soc_end,
            "range_start_km": trip.get("range_start_km"),
            "range_end_km": self._range_km(),
            "start_lat": trip.get("start_lat"),
            "start_lon": trip.get("start_lon"),
            "end_lat": lat,
            "end_lon": lon,
            "start_place": trip.get("start_place"),
            "end_place": place,
            "park_hours": trip.get("park_hours"),
            "park_soc_drop": trip.get("park_soc_drop"),
            "park_charged": trip.get("park_charged"),
            "points": points,
        }
        await self.hass.async_add_executor_job(self.db.add_trip, record)
        self._state["park"] = {"ts": record["end_ts"], "soc": soc_end, "charged": False}
        self._save()
        self._notify()

    # ----- charging -------------------------------------------------------------------

    @callback
    def _on_charging(self, new: State | None) -> None:
        if new is None or new.state in UNKNOWN_STATES:
            return
        if new.state == "on":
            if self._charge_end_timer:
                self._charge_end_timer()
                self._charge_end_timer = None
            if not self._state.get("charge"):
                self._start_charge()
        elif self._state.get("charge"):
            self._schedule_charge_end(CHARGE_GAP_MINUTES * 60)

    @callback
    def _start_charge(self) -> None:
        pos = self._position()
        place, _ = self._place(pos[0], pos[1]) if pos else (None, None)
        self._state["charge"] = {
            "start_ts": time.time(),
            "soc_start": self._soc(),
            "place": place,
            "at_home": self._at_home(),
            "kwh_meter": 0.0,
            "cost": 0.0,
            "max_power_kw": 0.0,
        }
        park = self._state.setdefault("park", self._park_snapshot())
        park["charged"] = True
        self._save()
        self._notify()

    @callback
    def _schedule_charge_end(self, delay: float) -> None:
        if self._charge_end_timer:
            self._charge_end_timer()

        @callback
        def _fire(_now) -> None:
            self._charge_end_timer = None
            self.hass.async_create_task(self._async_end_charge())

        self._charge_end_timer = async_call_later(
            self.hass, timedelta(seconds=delay), _fire
        )

    async def _async_end_charge(self) -> None:
        charge = self._state.get("charge")
        if charge is None:
            return
        self._state["charge"] = None
        soc_end = self._soc()
        soc_start = charge.get("soc_start")
        cap = self.conf.get(CONF_CAPACITY) or 0
        kwh_car = (
            round((soc_end - soc_start) / 100 * cap, 2)
            if soc_end is not None and soc_start is not None and cap
            else None
        )
        meter = charge.get("kwh_meter") or 0.0
        if meter <= 0 and (kwh_car is None or kwh_car <= 0):
            self._save()
            self._notify()
            return  # nothing was actually added
        record = {
            "car": self.car_id,
            "start_ts": charge["start_ts"],
            "end_ts": charge.get("last_ts") or time.time(),
            "soc_start": soc_start,
            "soc_end": soc_end,
            "kwh_car": kwh_car,
            "kwh_meter": round(meter, 3) if meter > 0 else None,
            "cost": round(charge.get("cost") or 0.0, 4) if meter > 0 else None,
            "max_power_kw": charge.get("max_power_kw") or None,
            "place": charge.get("place"),
            "at_home": charge.get("at_home"),
        }
        await self.hass.async_add_executor_job(self.db.add_charge, record)
        park = self._state.setdefault("park", self._park_snapshot())
        park["soc"] = soc_end  # drain is measured from the end of the charge
        park["ts"] = record["end_ts"]
        park["charged"] = False
        self._save()
        self._notify()

    @callback
    def _on_meter(self, event: Event[EventStateChangedData]) -> None:
        charge = self._state.get("charge")
        if charge is None or not charge.get("at_home"):
            return
        old = _num(event.data["old_state"])
        new = _num(event.data["new_state"])
        if old is None or new is None:
            return
        step = new - old
        if step <= 0 or step > MAX_METER_STEP_KWH:
            return
        price = None
        if self.conf.get(CONF_PRICE):
            price = _num(self.hass.states.get(self.conf[CONF_PRICE]))
        charge["kwh_meter"] = (charge.get("kwh_meter") or 0.0) + step
        if price is not None:
            charge["cost"] = (charge.get("cost") or 0.0) + step * price
        charge["last_ts"] = time.time()
        self._save()

    @callback
    def _on_power(self, event: Event[EventStateChangedData]) -> None:
        charge = self._state.get("charge")
        power = _num(event.data["new_state"])
        if charge is None or power is None:
            return
        if power > (charge.get("max_power_kw") or 0):
            charge["max_power_kw"] = power
            self._save()
