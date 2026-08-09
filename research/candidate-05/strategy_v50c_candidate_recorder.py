"""Candidate 05 v50c recorder with contingent-role fallback."""
from __future__ import annotations

import strategy_v50b_candidate_recorder as _base
from v50_order_capture_v2 import bracket_geometry

_base.bracket_geometry=bracket_geometry


class RobustActualOrderCandidateRecorderStrategy(_base.ActualOrderCandidateRecorderStrategy):
    pass


CandidateStrategy=RobustActualOrderCandidateRecorderStrategy
StrategyClass=RobustActualOrderCandidateRecorderStrategy
