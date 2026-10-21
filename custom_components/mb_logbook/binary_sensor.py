"""Trip in progress / charging session in progress."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import LogbookEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([TripActive(entry), ChargeActive(entry)])


class TripActive(LogbookEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.MOVING

    def __init__(self, entry) -> None:
        super().__init__(entry, "trip_active")

    @property
    def is_on(self) -> bool:
        return self._logger.trip_active

    @property
    def extra_state_attributes(self):
        trip = self._logger.current_trip
        if not trip:
            return None
        return {
            "started": dt_util.utc_from_timestamp(trip["start_ts"]).isoformat(),
            "from": trip.get("start_place"),
            "battery_start": trip.get("soc_start"),
        }


class ChargeActive(LogbookEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, entry) -> None:
        super().__init__(entry, "charge_active")

    @property
    def is_on(self) -> bool:
        return self._logger.charge_active
