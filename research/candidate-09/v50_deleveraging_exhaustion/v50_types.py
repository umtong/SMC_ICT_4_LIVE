"""Shared state records for Candidate 09 v50."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

MINUTE_NS = 60_000_000_000
EVENT_MINUTES = 5
PUBLICATION_DELAY_MINUTES = 5


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


@dataclass(slots=True)
class DeleveragingWatch:
    scenario_id: str
    created_index: int
    expires_index: int
    shock_direction: int
    reversal_side: int
    boundary: float
    shock_extreme: float
    reversal_structure: float
    event_poc: float
    atr: float
    details: dict[str, Any]
