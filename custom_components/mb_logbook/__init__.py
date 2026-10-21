"""MB Logbook: trips, parked drain and charging sessions for Mercedes EVs.

Reads the entities of the mbapi2020 integration (or any car exposing an
odometer, battery %, ignition and GPS tracker) and keeps a permanent local
log. Nothing is sent outside Home Assistant.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DB_FILENAME, DOMAIN, VERSION, entry_conf
from .db import LogbookDB
from .logger import CarLogger
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)

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
    await _async_register_card(hass)
    return db


async def _async_register_card(hass: HomeAssistant) -> None:
    """Keep a dashboard resource for the card, versioned so browsers refetch it.

    A Lovelace resource loads in every browser on every dashboard visit; the
    frontend's extra-JS list is baked into a cached index page, which some
    browsers (Firefox) kept serving without the card.
    """
    url = f"{CARD_URL}?v={VERSION}"
    try:
        resources = hass.data[LOVELACE_DATA].resources
        if not isinstance(resources, ResourceStorageCollection):
            add_extra_js_url(hass, url)  # YAML-mode dashboards: can't edit resources
            return
        await resources.async_get_info()  # loads the collection on first use
        for item in resources.async_items():
            if item["url"].split("?")[0] == CARD_URL:
                if item["url"] != url:
                    await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
                return
        await resources.async_create_item({"res_type": "module", "url": url})
    except Exception:  # noqa: BLE001 - never block setup over the card
        _LOGGER.warning("Couldn't register the card as a dashboard resource; using extra JS")
        add_extra_js_url(hass, url)


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
