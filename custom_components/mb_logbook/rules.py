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


def after_charge(park: dict[str, Any] | None, ts: float, soc: float | None) -> dict[str, Any]:
    """A charge ended: keep the park, restart the drain baseline from here."""
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


def trip_soc_end(
    soc_now: float | None, charge: dict[str, Any] | None, off_ts: float | None
) -> float | None:
    """Battery at the end of a trip.

    If charging began before the trip was closed, the current reading already
    includes charge; the level when charging began is the trip's end level.
    """
    if charge and charge.get("soc_start") is not None:
        if off_ts is None or charge.get("start_ts", 0) >= off_ts - 120:
            return charge["soc_start"]
    return soc_now
