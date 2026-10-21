"""MB Logbook: trips, parked drain and charging sessions for Mercedes EVs.

Reads the entities of the mbapi2020 integration (or any car exposing an
odometer, battery %, ignition and GPS tracker) and keeps a permanent local
log. Nothing is sent outside Home Assistant.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import DB_FILENAME, DOMAIN, VERSION, entry_conf
from .db import LogbookDB
from .importer import async_import_history
from .logger import CarLogger
from .websocket import async_register_websocket

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
STATIC_URL = "/mb_logbook"
CARD_URL = f"{STATIC_URL}/mb-logbook-card.js"
PANEL_PATH = "mb-logbook"

type LogbookConfigEntry = ConfigEntry[CarLogger]


async def _async_setup_shared(hass: HomeAssistant) -> LogbookDB:
    data = hass.data.setdefault(DOMAIN, {})
    if "db" in data:
        return data["db"]
    db = LogbookDB(hass.config.path(DB_FILENAME))
    await hass.async_add_executor_job(db.open)
    data["db"] = db
    async_register_websocket(hass)
    folder = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(folder), True)]
    )
    await _async_register_card(hass)
    await _async_register_panel(hass)
    return db


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Sidebar page: overview charts and the full logbook."""
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="mb-logbook-panel",
        frontend_url_path=PANEL_PATH,
        module_url=f"{STATIC_URL}/mb-logbook-panel.js?v={VERSION}",
        sidebar_title="MB Logbook",
        sidebar_icon="mdi:notebook-outline",
        require_admin=False,
        embed_iframe=False,
        config={"card_url": f"{CARD_URL}?v={VERSION}"},
    )


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


async def _async_import(hass: HomeAssistant, entry: LogbookConfigEntry, days: int) -> dict:
    """Import history up to when this car was added.

    Everything the live logger writes starts after the entry was created, so
    the creation time cleanly splits imported rows from live ones.
    """
    return await async_import_history(hass, entry.runtime_data, entry.created_at, days)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _service(call: ServiceCall) -> ServiceResponse:
        days = call.data.get("days", 365)
        wanted = call.data.get("config_entry_id")
        results = {}
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if wanted and entry.entry_id != wanted:
                continue
            results[entry.title] = await _async_import(hass, entry, days)
        return results

    hass.services.async_register(
        DOMAIN,
        "import_history",
        _service,
        schema=vol.Schema(
            {
                vol.Optional("config_entry_id"): cv.string,
                vol.Optional("days", default=365): vol.All(vol.Coerce(int), vol.Range(min=1, max=3650)),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LogbookConfigEntry) -> bool:
    db = await _async_setup_shared(hass)
    logger = CarLogger(hass, car_id_for(entry), entry_conf(entry), db)
    await logger.async_start()
    entry.runtime_data = logger
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not logger.imported:
        # First run for this car: fill in everything the recorder still has.
        async def _first_import(_hass: HomeAssistant) -> None:
            try:
                await _async_import(hass, entry, 365)
            except Exception:  # noqa: BLE001 - an import problem must not stop logging
                _LOGGER.exception("History import failed for %s", entry.title)

        entry.async_on_unload(async_at_started(hass, _first_import))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LogbookConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok
