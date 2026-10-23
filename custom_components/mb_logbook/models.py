"""Usable battery capacity from the model name mbapi2020 gives the car's device
(for example "EQS 450+ Exclusive Luxury Saloon"). Only used to pre-fill the setup
form: capacities changed between model years, so the user checks it."""

from __future__ import annotations

import re

# First match wins, so more specific names come first. Usable kWh, approximate.
_CAPACITIES: list[tuple[str, float]] = [
    (r"\bEQS\b.*\bSUV\b", 108.4),
    (r"\bEQS\b", 107.8),
    (r"\bEQE\b.*\bSUV\b", 89.0),
    (r"\bEQE\b", 89.0),
    (r"\bEQC\b", 80.0),
    (r"\bEQV\b", 90.0),
    (r"\bEQA\b", 66.5),
    (r"\bEQB\b", 66.5),
    (r"\bEQT\b", 45.0),
    # The electric CLA (2025 on); "CLA 250 e" and the petrol CLAs don't match.
    (r"\bCLA\s*(250\+|350\b|200\b)|\bCLA\b.*\bEQ\b", 85.0),
    (r"\bG\s*580\b", 116.0),
    (r"\beSprinter\b", 113.0),
    (r"\beVito\b", 90.0),
]


def capacity_for_model(model: str | None) -> float | None:
    if not model:
        return None
    for pattern, kwh in _CAPACITIES:
        if re.search(pattern, model, re.IGNORECASE):
            return kwh
    return None
