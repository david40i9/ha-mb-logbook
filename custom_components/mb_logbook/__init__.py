"""MB Logbook: trips, parked drain and charging sessions for Mercedes EVs.

Reads the entities of the mbapi2020 integration (or any car exposing an
odometer, battery %, ignition and GPS tracker) and keeps a permanent local
log. Nothing is sent outside Home Assistant.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DB_FILENAME, DOMAIN, VERSION, entry_conf
from .db import LogbookDB
from .logger import CarLogger
from .websocket import async_register_websocket

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CARD_URL = "/mb_logbook/mb-logbook-card.js"

type LogbookConfigEntry = ConfigEntry[CarLogger]


async def _async_setup_shared(hass: HomeAssistant) -> LogbookDB:
    data = hass.data.setdefault(DOMAIN, {})
    if "db" in data:
        return data["db"]
    db = LogbookDB(hass.config.path(DB_FILENAME))
    await hass.async_add_executor_job(db.open)
    data["db"] = db
    async_register_websocket(hass)
    card = Path(__file__).parent / "frontend" / "mb-logbook-card.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(card), True)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    return db


def car_id_for(entry: ConfigEntry) -> str:
    return entry.unique_id or entry.entry_id


async def async_setup_entry(hass: HomeAssistant, entry: LogbookConfigEntry) -> bool:
    db = await _async_setup_shared(hass)
    logger = CarLogger(hass, car_id_for(entry), entry_conf(entry), db)
    await logger.async_start()
    entry.runtime_data = logger
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LogbookConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok
