"""Candidate 09 v50: forced-deleveraging exhaustion reversal.

A five-minute directional shock becomes a forced-deleveraging context only after
causally delayed open interest shows contraction while premium expanded in the
shock direction. Same-direction flow must then fail to extend price during the
full publication delay. No order exists until a later opposite initiative leg
breaks the delay balance with aligned flow, efficiency, delta and POC migration.
The old balance boundary is the target and the full shock extreme invalidates.
"""
from __future__ import annotations

from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy
from v50_execution import Candidate50ExecutionMixin
from v50_state import Candidate50StateMixin
from v50_types import DeleveragingWatch, EVENT_MINUTES, PUBLICATION_DELAY_MINUTES


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate50_require_forced_deleveraging: bool = True
    candidate50_event_minutes: int = EVENT_MINUTES
    candidate50_publication_delay_minutes: int = PUBLICATION_DELAY_MINUTES


class Candidate16Strategy(
    Candidate50ExecutionMixin,
    Candidate50StateMixin,
    _Candidate35Strategy,
):
    """Cause -> exhaustion -> new opposite auction leg -> cost-aware bracket."""


__all__ = ["Candidate16Config", "Candidate16Strategy", "DeleveragingWatch"]
