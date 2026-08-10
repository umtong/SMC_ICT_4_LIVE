"""Strong-state wrapper for the pinned Candidate 47 IchiFan entry.

Candidate 47 already computes one causal source score from three quantities
inside the public mechanism: fan acceleration gain, fan magnitude and distance
above the shifted Ichimoku cloud.  The score was previously used only to choose
between simultaneous symbols.  This focused experiment asks whether the same
quantity can also distinguish a complete strong-fan market state before entry.

No exit, structural stop, risk, cost, arbitration ordering or source indicator
is changed.  ``ichifan_min_entry_score=0`` is the exact control.  The only
selected experiment is the round score 17, chosen after chronological
trade-anatomy development and requiring a separate frozen evaluation before it
can be treated as evidence of generalization.
"""
from __future__ import annotations

from typing import Any

import ichifan_structural_strategy as _struct


class Candidate51IchiFanStrongScoreConfig(
    _struct.Candidate47IchiFanStructuralConfig,
    frozen=True,
):
    ichifan_min_entry_score: float = 0.0


class Candidate51IchiFanStrongScoreStrategy(
    _struct.Candidate47IchiFanStructuralStrategy,
):
    def __init__(self, config: Candidate51IchiFanStrongScoreConfig) -> None:
        super().__init__(config)
        minimum = float(config.ichifan_min_entry_score)
        if minimum not in {0.0, 17.0}:
            raise ValueError("focused strong-state experiment supports only score 0 or 17")
        self.diagnostics.update(
            {
                "ichifan_min_entry_score": minimum,
                "ichifan_score_evaluations": 0,
                "ichifan_score_rejections": 0,
                "ichifan_score_passes": 0,
                "ichifan_rejected_score_sum": 0.0,
                "ichifan_accepted_score_sum": 0.0,
            }
        )

    def _submit_decision(self, decision, ts_event: int) -> None:
        self.diagnostics["ichifan_score_evaluations"] += 1
        score = float(decision.score)
        minimum = float(self.config.ichifan_min_entry_score)
        if score + 1e-12 < minimum:
            self.diagnostics["ichifan_score_rejections"] += 1
            self.diagnostics["ichifan_rejected_score_sum"] += score
            self._event(
                "ICHIFAN_STRONG_SCORE_REJECTED",
                ts_event,
                symbol=decision.symbol,
                source_score=score,
                minimum_score=minimum,
                diagnostics=dict(decision.diagnostics),
            )
            return
        self.diagnostics["ichifan_score_passes"] += 1
        self.diagnostics["ichifan_accepted_score_sum"] += score
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "ichifan_min_entry_score": minimum,
                    "ichifan_source_score": score,
                    "ichifan_score_state": "STRONG_FAN" if minimum > 0.0 else "SOURCE_CONTROL",
                }
            )


Candidate35Config = Candidate51IchiFanStrongScoreConfig
Candidate35Strategy = Candidate51IchiFanStrongScoreStrategy

__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "Candidate51IchiFanStrongScoreConfig",
    "Candidate51IchiFanStrongScoreStrategy",
]
