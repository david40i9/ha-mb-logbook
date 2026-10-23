"""Config flow: pick a car, confirm its entities, add charger/price if wanted."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .models import capacity_for_model
from .const import (
    CONF_CAPACITY,
    CONF_CAR_NAME,
    CONF_CHARGE_POWER,
    CONF_CHARGER_ENERGY,
    CONF_CHARGING,
    CONF_CURRENCY,
    CONF_DISTANCE_UNIT,
    CONF_HOME_ZONE,
    CONF_IGNITION,
    CONF_MIN_DISTANCE,
    CONF_ODOMETER,
    CONF_OUTSIDE_TEMP,
    CONF_PRICE,
    CONF_RANGE,
    CONF_SOC,
    CONF_STOP_MINUTES,
    CONF_SUMMARY_NOTIFY,
    CONF_TRACKER,
    CONF_TRIP_DISTANCE,
    DEFAULT_HOME_ZONE,
    DEFAULT_MIN_DISTANCE_KM,
    DEFAULT_STOP_MINUTES,
    DOMAIN,
    MB_KEYS,
    UNIT_KM,
    UNIT_MI,
)

CONF_DEVICE = "device_id"
# Currencies for charging costs (any ISO 4217 code can be typed in)
CURRENCIES = ["GBP", "EUR", "USD", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "AUD", "NZD", "CAD", "JPY", "CNY", "KRW", "INR", "ZAR"]


def _detect(hass: HomeAssistant, device_id: str) -> tuple[dict[str, str], str | None]:
    """Find the car's entities from mbapi2020's '<vin>_<key>' unique IDs."""
    reg = er.async_get(hass)
    found: dict[str, str] = {}
    vin: str | None = None
    for ent in er.async_entries_for_device(reg, device_id):
        if ent.platform != "mbapi2020" or "_" not in ent.unique_id:
            continue
        prefix, _, key = ent.unique_id.partition("_")
        for conf_key, (domain, mb_key) in MB_KEYS.items():
            if ent.domain == domain and key == mb_key:
                found[conf_key] = ent.entity_id
                vin = vin or prefix
    return found, vin


def _unit_from(hass: HomeAssistant, entity_id: str | None) -> str:
    st = hass.states.get(entity_id) if entity_id else None
    unit = (st.attributes.get("unit_of_measurement") or "") if st else ""
    return UNIT_MI if unit.lower() in ("mi", "miles") else UNIT_KM


def _req(key: str, d: dict[str, Any]) -> vol.Required:
    """Required field pre-filled only when a value is known."""
    return vol.Required(key, description={"suggested_value": d.get(key)})


def _schema(hass: HomeAssistant, d: dict[str, Any], with_name: bool) -> vol.Schema:
    sens = EntitySelector(EntitySelectorConfig(domain="sensor"))
    fields: dict[Any, Any] = {}
    if with_name:
        fields[vol.Required(CONF_CAR_NAME, default=d.get(CONF_CAR_NAME, ""))] = TextSelector()
    fields.update(
        {
            _req(CONF_ODOMETER, d): sens,
            vol.Optional(
                CONF_TRIP_DISTANCE, description={"suggested_value": d.get(CONF_TRIP_DISTANCE)}
            ): sens,
            _req(CONF_SOC, d): sens,
            vol.Optional(
                CONF_IGNITION, description={"suggested_value": d.get(CONF_IGNITION)}
            ): sens,
            _req(CONF_TRACKER, d): EntitySelector(EntitySelectorConfig(domain="device_tracker")),
            vol.Optional(
                CONF_RANGE, description={"suggested_value": d.get(CONF_RANGE)}
            ): sens,
            vol.Optional(
                CONF_CHARGING, description={"suggested_value": d.get(CONF_CHARGING)}
            ): EntitySelector(EntitySelectorConfig(domain="binary_sensor")),
            vol.Optional(
                CONF_CHARGE_POWER, description={"suggested_value": d.get(CONF_CHARGE_POWER)}
            ): sens,
            vol.Required(CONF_CAPACITY, default=d.get(CONF_CAPACITY, 0)): NumberSelector(
                NumberSelectorConfig(min=0, max=250, step=0.1, mode=NumberSelectorMode.BOX,
                                     unit_of_measurement="kWh")
            ),
            vol.Optional(
                CONF_CHARGER_ENERGY,
                description={"suggested_value": d.get(CONF_CHARGER_ENERGY)},
            ): EntitySelector(EntitySelectorConfig(domain="sensor", device_class="energy")),
            vol.Required(
                CONF_CURRENCY, default=d.get(CONF_CURRENCY) or hass.config.currency or "EUR"
            ): SelectSelector(SelectSelectorConfig(options=CURRENCIES, custom_value=True, sort=False)),
            vol.Optional(
                CONF_PRICE, description={"suggested_value": d.get(CONF_PRICE)}
            ): sens,
            vol.Required(
                CONF_HOME_ZONE, default=d.get(CONF_HOME_ZONE, DEFAULT_HOME_ZONE)
            ): EntitySelector(EntitySelectorConfig(domain="zone")),
            vol.Required(
                CONF_DISTANCE_UNIT, default=d.get(CONF_DISTANCE_UNIT, UNIT_KM)
            ): SelectSelector(SelectSelectorConfig(options=[UNIT_KM, UNIT_MI])),
            vol.Required(
                CONF_STOP_MINUTES, default=d.get(CONF_STOP_MINUTES, DEFAULT_STOP_MINUTES)
            ): NumberSelector(NumberSelectorConfig(min=1, max=30, step=1,
                                                   mode=NumberSelectorMode.BOX,
                                                   unit_of_measurement="min")),
            vol.Required(
                CONF_MIN_DISTANCE, default=d.get(CONF_MIN_DISTANCE, DEFAULT_MIN_DISTANCE_KM)
            ): NumberSelector(NumberSelectorConfig(min=0, max=10, step=0.1,
                                                   mode=NumberSelectorMode.BOX,
                                                   unit_of_measurement="km")),
            vol.Optional(
                CONF_OUTSIDE_TEMP, description={"suggested_value": d.get(CONF_OUTSIDE_TEMP)}
            ): EntitySelector(EntitySelectorConfig(filter=[
                {"domain": "weather"}, {"domain": "sensor", "device_class": "temperature"},
            ])),
            vol.Optional(
                CONF_SUMMARY_NOTIFY, description={"suggested_value": d.get(CONF_SUMMARY_NOTIFY)}
            ): SelectSelector(SelectSelectorConfig(options=_notify_targets(hass), custom_value=True)),
        }
    )
    return vol.Schema(fields)


def _notify_targets(hass: HomeAssistant) -> list[dict[str, str]]:
    """Where the monthly summary can go: a notification in HA, or a notify service."""
    targets = [{"value": "persistent_notification", "label": "Home Assistant notification"}]
    for service in sorted(hass.services.async_services_for_domain("notify")):
        if service not in ("persistent_notification", "send_message"):
            targets.append({"value": f"notify.{service}", "label": f"notify.{service}"})
    return targets


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in user_input.items() if v not in (None, "")}


class MBLogbookConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one car."""

    VERSION = 1

    def __init__(self) -> None:
        self._defaults: dict[str, Any] = {}
        self._vin: str | None = None
        self._model: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            device_id = user_input.get(CONF_DEVICE)
            if device_id:
                found, self._vin = _detect(self.hass, device_id)
                self._defaults.update(found)
                device = dr.async_get(self.hass).async_get(device_id)
                if device:
                    self._defaults[CONF_CAR_NAME] = device.name_by_user or device.name or ""
                    # Pre-fill the usable battery size from the model name (checked by the user).
                    if (kwh := capacity_for_model(device.model)) and not self._defaults.get(CONF_CAPACITY):
                        self._defaults[CONF_CAPACITY] = kwh
                        self._model = device.model
                self._defaults[CONF_DISTANCE_UNIT] = _unit_from(
                    self.hass, found.get(CONF_ODOMETER)
                )
            return await self.async_step_entities()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DEVICE): DeviceSelector(
                        DeviceSelectorConfig(integration="mbapi2020")
                    )
                }
            ),
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            unique = self._vin or data[CONF_ODOMETER]
            await self.async_set_unique_id(unique)
            self._abort_if_unique_id_configured()
            if not data.get(CONF_CAPACITY):
                errors[CONF_CAPACITY] = "capacity_required"
            else:
                return self.async_create_entry(title=data[CONF_CAR_NAME], data=data)
            self._defaults.update(user_input)
        return self.async_show_form(
            step_id="entities",
            data_schema=_schema(self.hass, self._defaults, with_name=True),
            errors=errors,
            description_placeholders={
                "model_note": f" Battery capacity was filled in for the {self._model}; check it against your car." if self._model else ""
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> MBLogbookOptionsFlow:
        return MBLogbookOptionsFlow()


class MBLogbookOptionsFlow(OptionsFlowWithReload):
    """Change entities or settings later."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        if user_input is not None:
            data = _clean(user_input)
            # The name lives in the entry's data and title (shown in the car switcher,
            # the device and its sensors' names); entity IDs don't change.
            name = str(data.pop(CONF_CAR_NAME, "") or "").strip()
            if name and name != entry.title:
                self.hass.config_entries.async_update_entry(
                    entry, title=name, data={**entry.data, CONF_CAR_NAME: name}
                )
            return self.async_create_entry(data=data)
        current = {**entry.data, **entry.options, CONF_CAR_NAME: entry.data.get(CONF_CAR_NAME) or entry.title}
        return self.async_show_form(
            step_id="init", data_schema=_schema(self.hass, current, with_name=True)
        )
