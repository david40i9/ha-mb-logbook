"""Park and battery rules shared by the live logger and the history importer.

A park runs from the end of one trip to the start of the next.

- ``park_hours`` is the whole park.
- If the car charged during the park, the park is marked charged, and idle
  drain is measured only from the end of the (last) charge:
  ``park_drain_hours`` and ``park_soc_drop`` cover that part.
- A rise of 2% or more with no recorded charge can only be an unrecorded
  charge; the park is marked charged and no drain is measured for it.

Park state: {"ts": park start, "drain_ts": drain baseline time,
             "soc": drain baseline %, "charged": bool}
"""

from __future__ import annotations

from typing import Any


def new_park(ts: float, soc: float | None, charging: bool) -> dict[str, Any]:
    """Park starting when a trip ends (charging if a charge already began)."""
    return {"ts": ts, "drain_ts": ts, "soc": soc, "charged": charging}


def charging_into_park(charge: dict[str, Any] | None, off_ts: float | None) -> bool:
    """Did this charge start after the trip ended (plugged in on arrival)?

    A session that started earlier is left over from before the drive (it is
    only closed after a gap), so it doesn't belong to the new park.
    """
    if not charge:
        return False
    return off_ts is None or charge.get("start_ts", 0) >= off_ts - 120


def after_charge(park: dict[str, Any] | None, ts: float, soc: float | None) -> dict[str, Any]:
    """A charge ended: keep the park, restart the drain baseline from here."""
    if park and park.get("ts") and ts < park["ts"]:
        return park  # it finished before this park began: it belonged to the last one
    park = dict(park or new_park(ts, soc, True))
    park["charged"] = True
    park["drain_ts"] = ts
    park["soc"] = soc
    return park


def park_fields(park: dict[str, Any] | None, now: float, soc: float | None) -> dict[str, Any]:
    """Fields recorded on the trip that ends this park."""
    park = park or {}
    ts = park.get("ts")
    drain_ts = park.get("drain_ts") or ts
    base = park.get("soc")
    charged = bool(park.get("charged"))
    drop = round(base - soc, 1) if base is not None and soc is not None else None
    if drop is not None and drop <= -2:
        # Battery went up without a recorded charge (or after one): unmeasurable.
        charged = True
        drop = None
    return {
        "park_hours": round((now - ts) / 3600, 2) if ts else None,
        "park_drain_hours": round((now - drain_ts) / 3600, 2) if drain_ts else None,
        "park_soc_drop": drop,
        "park_charged": charged,
    }


def add_power_sample(
    charge: dict[str, Any], ts: float, power_kw: float | None, price: float | None
) -> None:
    """Integrate the car's charging power into energy and cost.

    Used when there's no home charger meter: each interval is costed at the
    price in force during it. The car only reports power when it changes, so a
    steady charge has long intervals; a gap is capped at 6 h so a stale reading
    can't run away.
    """
    last = charge.get("pw_last")
    if last:
        dt_h = min(max(ts - last[0], 0.0), 6 * 3600) / 3600
        kwh = (last[1] or 0.0) * dt_h
        charge["kwh_power"] = (charge.get("kwh_power") or 0.0) + kwh
        if last[2] is not None:
            charge["cost_power"] = (charge.get("cost_power") or 0.0) + kwh * last[2]
    charge["pw_last"] = [ts, power_kw if power_kw is not None else (last[1] if last else 0.0), price]


def charge_cost(charge: dict[str, Any], has_price: bool) -> tuple[float | None, str | None]:
    """(cost, basis) for a finished session: home meter first, else charging power."""
    meter = charge.get("kwh_meter") or 0.0
    if meter > 0:
        return round(charge.get("cost") or 0.0, 4), "meter"
    if has_price and charge.get("at_home") and (charge.get("kwh_power") or 0) > 0:
        return round(charge.get("cost_power") or 0.0, 4), "power"
    return None, None


def trip_soc_end(
    soc_now: float | None, charge: dict[str, Any] | None, off_ts: float | None
) -> float | None:
    """Battery at the end of a trip.

    If charging began before the trip was closed, the current reading already
    includes charge; the level when charging began is the trip's end level.
    """
    if charging_into_park(charge, off_ts) and charge.get("soc_start") is not None:
        return charge["soc_start"]
    return soc_now
