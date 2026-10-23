"""Opt-in monthly summary: last month's figures, sent on the 1st at 09:00."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAPACITY,
    CONF_CAR_NAME,
    CONF_CURRENCY,
    CONF_DISTANCE_UNIT,
    CONF_SUMMARY_NOTIFY,
    IDLE_MIN_HOURS,
    KM_PER_MI,
    UNIT_MI,
)
from .logger import CarLogger

_LOGGER = logging.getLogger(__name__)

PERSISTENT = "persistent_notification"


# Symbols for common currencies; anything else shows its code ("123.45 SEK").
_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "JPY": "¥", "CHF": "CHF "}
_NO_DECIMALS = {"JPY", "KRW", "HUF", "ISK"}


def _money(currency: str, value: float) -> str:
    amount = f"{value:,.0f}" if currency in _NO_DECIMALS else f"{value:,.2f}"
    symbol = _SYMBOLS.get(currency)
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}"


def _last_month(now: datetime) -> tuple[datetime, datetime]:
    end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = (end.replace(year=end.year - 1, month=12) if end.month == 1 else end.replace(month=end.month - 1))
    return start, end


async def async_build(hass: HomeAssistant, logger: CarLogger, now: datetime) -> tuple[str, str]:
    """(title, message) for the month before ``now``."""
    conf = logger.conf
    start, end = _last_month(now)
    db = logger.db
    t = await hass.async_add_executor_job(db.totals, logger.car_id, start.timestamp(), end.timestamp())
    parks = await hass.async_add_executor_job(db.idle_parks, logger.car_id, start.timestamp(), IDLE_MIN_HOURS)
    miles = conf.get(CONF_DISTANCE_UNIT) == UNIT_MI
    k = 1 / KM_PER_MI if miles else 1
    unit = "mi" if miles else "km"
    cap = float(conf.get(CONF_CAPACITY) or 0)
    kwh = t["soc_used"] / 100 * cap
    lines = [f"{t['trips']} trips, {t['distance_km'] * k:.0f} {unit}"]
    if kwh and t["distance_km_with_soc"]:
        eff = (
            f"{t['distance_km_with_soc'] / KM_PER_MI / kwh:.2f} mi/kWh"
            if miles else f"{kwh / t['distance_km_with_soc'] * 100:.1f} kWh/100km"
        )
        lines.append(f"Used about {kwh:.0f} kWh driving ({eff})")
    if t["charges"]:
        cost = f", {_money(conf.get(CONF_CURRENCY) or hass.config.currency, t['charge_cost'])}" if t["charge_cost"] else ""
        lines.append(f"Charged {t['charged_kwh']:.0f} kWh in {t['charges']} sessions{cost}")
    hours = sum(h for h, _ in parks)
    if hours:
        lines.append(f"Idle drain {sum(d for _, d in parks) / hours * 24:.2f} %/day while parked")
    title = f"{conf[CONF_CAR_NAME]}: {dt_util.as_local(start).strftime('%B %Y')}"
    return title, "\n".join(lines)


async def async_send(hass: HomeAssistant, logger: CarLogger, now: datetime) -> None:
    target = logger.conf.get(CONF_SUMMARY_NOTIFY)
    if not target:
        return
    title, message = await async_build(hass, logger, now)
    if target == PERSISTENT:
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": title, "message": message, "notification_id": f"mb_logbook_{logger.car_id}_month"},
        )
        return
    domain, _, service = target.partition(".")
    await hass.services.async_call(domain, service, {"title": title, "message": message})


@callback
def async_setup_summary(hass: HomeAssistant, logger: CarLogger):
    """Schedule the summary if the car has one set up; returns the unsubscribe."""
    if not logger.conf.get(CONF_SUMMARY_NOTIFY):
        return None

    async def _tick(now: datetime) -> None:
        if dt_util.as_local(now).day != 1:
            return
        try:
            await async_send(hass, logger, dt_util.as_local(now))
        except Exception:  # noqa: BLE001 - a notify problem must not break logging
            _LOGGER.exception("Monthly summary for %s failed", logger.conf.get(CONF_CAR_NAME))

    return async_track_time_change(hass, _tick, hour=9, minute=0, second=0)
