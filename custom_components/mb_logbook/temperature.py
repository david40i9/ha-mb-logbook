"""Outside temperature for trips: a chosen sensor, or Home Assistant's local weather."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, State

from .const import CONF_OUTSIDE_TEMP

UNKNOWN = ("unknown", "unavailable", "")


def temp_source(hass: HomeAssistant, conf: dict[str, Any]) -> str | None:
    """The chosen entity, else the weather for HA's home location (met.no's
    weather.forecast_home on a default install), else any weather entity."""
    if conf.get(CONF_OUTSIDE_TEMP):
        return conf[CONF_OUTSIDE_TEMP]
    weather = sorted(hass.states.async_entity_ids("weather"))
    if "weather.forecast_home" in weather:
        return "weather.forecast_home"
    return weather[0] if weather else None


def temp_c(state: State | None, unit: str | None = None) -> float | None:
    """°C from a temperature sensor's state or a weather entity's attributes."""
    if state is None:
        return None
    if state.domain == "weather":
        value, unit = state.attributes.get("temperature"), state.attributes.get("temperature_unit")
    else:
        if state.state in UNKNOWN:
            return None
        value, unit = state.state, state.attributes.get("unit_of_measurement") or unit
    return to_c(value, unit)


def to_c(value: Any, unit: str | None) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round((v - 32) * 5 / 9 if unit == "°F" else v, 1)
