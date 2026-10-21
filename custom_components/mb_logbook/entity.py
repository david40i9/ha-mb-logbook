"""Shared base entity: one device per car, refreshed when the log changes."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import CONF_CAR_NAME, DOMAIN, SIGNAL_UPDATED, VERSION, entry_conf


class LogbookEntity(Entity):
    """Base class for MB Logbook entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, key: str) -> None:
        self._entry = entry
        self._logger = entry.runtime_data
        self._attr_unique_id = f"{self._logger.car_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._logger.car_id)},
            name=f"{entry_conf(entry)[CONF_CAR_NAME]} logbook",
            manufacturer="MB Logbook",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATED.format(self._logger.car_id), self._refresh
            )
        )

    async def _refresh(self) -> None:
        self.async_write_ha_state()
