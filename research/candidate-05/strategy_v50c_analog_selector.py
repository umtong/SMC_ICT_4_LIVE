"""Candidate 05 v50c selector with contingent-role fallback."""
from __future__ import annotations

import strategy_v50b_analog_selector as _base
from v50_order_capture_v2 import bracket_geometry

_base.bracket_geometry=bracket_geometry


class RobustActualOrderAnalogStrategy(_base.ActualOrderAnalogStrategy):
    pass


CandidateStrategy=RobustActualOrderAnalogStrategy
StrategyClass=RobustActualOrderAnalogStrategy
