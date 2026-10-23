"""Summary sensors per car (month totals, last trip, last charge, idle drain)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import time
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAPACITY,
    CONF_DISTANCE_UNIT,
    CONF_PRICE,
    IDLE_MIN_HOURS,
    KM_PER_MI,
    SIGNAL_UPDATED,
    UNIT_MI,
    entry_conf,
)
from .entity import LogbookEntity


@dataclass(frozen=True, kw_only=True)
class LogbookSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]
    attrs: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _month_start() -> float:
    now = dt_util.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


class Stats:
    """Recomputes every figure in one executor call and pushes them to sensors."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.logger = entry.runtime_data
        conf = entry_conf(entry)
        self.capacity = float(conf.get(CONF_CAPACITY) or 0)
        self.miles = conf.get(CONF_DISTANCE_UNIT) == UNIT_MI
        self.data: dict[str, Any] = {}
        self.entities: list[SensorEntity] = []

    def dist(self, km: float | None) -> float | None:
        if km is None:
            return None
        return round(km / KM_PER_MI if self.miles else km, 1)

    def efficiency(self, km: float, kwh: float) -> float | None:
        """mi/kWh when the car is set to miles, otherwise kWh/100 km."""
        if not km or not kwh:
            return None
        if self.miles:
            return round(km / KM_PER_MI / kwh, 2)
        return round(kwh / km * 100, 1)

    def _compute(self) -> dict[str, Any]:
        db, car = self.logger.db, self.logger.car_id
        month = db.totals(car, _month_start(), time.time() + 86400)
        last = db.last_trip(car)
        charge = db.last_charge(car)
        parks = db.idle_parks(car, time.time() - 30 * 86400, IDLE_MIN_HOURS)
        return {"month": month, "last": last, "charge": charge, "parks": parks}

    async def async_refresh(self, *_: Any) -> None:
        raw = await self.hass.async_add_executor_job(self._compute)
        cap = self.capacity
        month, last, charge = raw["month"], raw["last"], raw["charge"]
        month_kwh = month["soc_used"] / 100 * cap if cap else None
        last_kwh = None
        if last and last.get("soc_start") is not None and last.get("soc_end") is not None:
            last_kwh = max(last["soc_start"] - last["soc_end"], 0) / 100 * cap if cap else None
        # Battery % comes in whole steps, so pool the parks: total lost / total time.
        park_hours = sum(h for h, _ in raw["parks"])
        drain = sum(d for _, d in raw["parks"]) / park_hours * 24 if park_hours else None
        self.data = {
            "trips_month": month["trips"],
            "distance_month": self.dist(month["distance_km"]),
            "energy_month": round(month_kwh, 1) if month_kwh is not None else None,
            "efficiency_month": self.efficiency(month["distance_km_with_soc"], month_kwh or 0),
            "last_trip_distance": self.dist(last["distance_km"]) if last else None,
            "last_trip_energy": round(last_kwh, 1) if last_kwh is not None else None,
            "last_trip_efficiency": (
                self.efficiency(last["distance_km"], last_kwh) if last and last_kwh else None
            ),
            "last_trip": last,
            "idle_drain": round(drain, 2) if drain is not None else None,
            "idle_samples": len(raw["parks"]),
            "last_charge_energy": (
                (charge.get("kwh_meter") or charge.get("kwh_car")) if charge else None
            ),
            "last_charge_cost": round(charge["cost"], 2) if charge and charge.get("cost") else None,
            "last_charge": charge,
            "charged_month": round(month["charged_kwh"], 1),
            "charge_cost_month": round(month["charge_cost"], 2),
        }
        for ent in self.entities:
            if ent.hass is not None:
                ent.async_write_ha_state()


def _ts(value: float | None) -> str | None:
    return dt_util.utc_from_timestamp(value).isoformat() if value else None


def _trip_attrs(d: dict[str, Any]) -> dict[str, Any]:
    t = d.get("last_trip")
    if not t:
        return {}
    return {
        "start": _ts(t["start_ts"]),
        "end": _ts(t["end_ts"]),
        "from": t.get("start_place"),
        "to": t.get("end_place"),
        "battery_start": t.get("soc_start"),
        "battery_end": t.get("soc_end"),
    }


def _charge_attrs(d: dict[str, Any]) -> dict[str, Any]:
    c = d.get("last_charge")
    if not c:
        return {}
    return {
        "start": _ts(c["start_ts"]),
        "end": _ts(c["end_ts"]),
        "battery_start": c.get("soc_start"),
        "battery_end": c.get("soc_end"),
        "energy_added_car": c.get("kwh_car"),
        "energy_metered": c.get("kwh_meter"),
        "place": c.get("place"),
    }


def _descriptions(miles: bool, currency: str | None) -> list[LogbookSensorDescription]:
    dist_unit = UnitOfLength.MILES if miles else UnitOfLength.KILOMETERS
    eff_unit = "mi/kWh" if miles else "kWh/100km"
    descs = [
        LogbookSensorDescription(key="trips_month", value=lambda d: d.get("trips_month")),
        LogbookSensorDescription(key="distance_month", native_unit_of_measurement=dist_unit,
                                 suggested_unit_of_measurement=dist_unit, suggested_display_precision=1,
                                 device_class=SensorDeviceClass.DISTANCE,
                                 value=lambda d: d.get("distance_month")),
        LogbookSensorDescription(key="energy_month",
                                 native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                                 device_class=SensorDeviceClass.ENERGY,
                                 value=lambda d: d.get("energy_month")),
        LogbookSensorDescription(key="efficiency_month", native_unit_of_measurement=eff_unit,
                                 state_class=SensorStateClass.MEASUREMENT,
                                 value=lambda d: d.get("efficiency_month")),
        LogbookSensorDescription(key="last_trip_distance", native_unit_of_measurement=dist_unit,
                                 suggested_unit_of_measurement=dist_unit, suggested_display_precision=1,
                                 device_class=SensorDeviceClass.DISTANCE,
                                 value=lambda d: d.get("last_trip_distance"),
                                 attrs=_trip_attrs),
        LogbookSensorDescription(key="last_trip_energy",
                                 native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                                 device_class=SensorDeviceClass.ENERGY,
                                 value=lambda d: d.get("last_trip_energy")),
        LogbookSensorDescription(key="last_trip_efficiency", native_unit_of_measurement=eff_unit,
                                 value=lambda d: d.get("last_trip_efficiency")),
        LogbookSensorDescription(key="idle_drain", native_unit_of_measurement=f"{PERCENTAGE}/day",
                                 state_class=SensorStateClass.MEASUREMENT,
                                 value=lambda d: d.get("idle_drain"),
                                 attrs=lambda d: {"parks_counted": d.get("idle_samples"),
                                                  "window_days": 30,
                                                  "min_park_hours": IDLE_MIN_HOURS}),
        LogbookSensorDescription(key="last_charge_energy",
                                 native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                                 device_class=SensorDeviceClass.ENERGY,
                                 value=lambda d: d.get("last_charge_energy"),
                                 attrs=_charge_attrs),
        LogbookSensorDescription(key="charged_month",
                                 native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                                 device_class=SensorDeviceClass.ENERGY,
                                 value=lambda d: d.get("charged_month")),
    ]
    if currency:
        descs += [
            LogbookSensorDescription(key="last_charge_cost", native_unit_of_measurement=currency,
                                     device_class=SensorDeviceClass.MONETARY,
                                     value=lambda d: d.get("last_charge_cost")),
            LogbookSensorDescription(key="charge_cost_month", native_unit_of_measurement=currency,
                                     device_class=SensorDeviceClass.MONETARY,
                                     value=lambda d: d.get("charge_cost_month")),
        ]
    return descs


def _currency(hass: HomeAssistant, price_entity: str | None) -> str | None:
    if not price_entity:
        return None
    # Costs are stored in whole currency units (a p/kWh or c/kWh price is converted),
    # so they are in Home Assistant's own currency whatever the price sensor says.
    return hass.config.currency


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    stats = Stats(hass, entry)
    conf = entry_conf(entry)
    descs = _descriptions(stats.miles, _currency(hass, conf.get(CONF_PRICE)))
    stats.entities = [LogbookSensor(entry, stats, d) for d in descs]
    await stats.async_refresh()
    async_add_entities(stats.entities)
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_UPDATED.format(entry.runtime_data.car_id), stats.async_refresh
        )
    )
    # Month totals roll over at midnight on the 1st without any new trip.
    entry.async_on_unload(
        async_track_time_interval(hass, stats.async_refresh, timedelta(minutes=30))
    )


class LogbookSensor(LogbookEntity, SensorEntity):
    """One figure from the shared Stats."""

    entity_description: LogbookSensorDescription

    def __init__(self, entry, stats: Stats, desc: LogbookSensorDescription) -> None:
        super().__init__(entry, desc.key)
        self.entity_description = desc
        self._stats = stats

    async def async_added_to_hass(self) -> None:
        """Stats pushes updates, so skip the base dispatcher hook."""

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self._stats.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs is None:
            return None
        return self.entity_description.attrs(self._stats.data)
