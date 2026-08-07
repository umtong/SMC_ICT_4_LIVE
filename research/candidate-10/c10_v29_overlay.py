"""v29 independent-external-draw certificate over v28 and v27 costs.

A failed-auction reversal is executable only when its target is an independently
pre-existing external liquidity hazard.  Targets inferred only from source-range
acceptance or contemporaneous context are diagnostic, not independent draws.
The exact ablation restores v28 without this certificate.
"""
from __future__ import annotations

from dataclasses import replace
import os
from typing import Any

from c10_v28_overlay import (  # re-export for the patched Candidate 11 runner
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
)


def certify_plan(plan: Any, decision: Any) -> Any:
    if (
        os.environ.get("C10_V29_ABLATE_EXTERNAL_DRAW", "0") == "1"
        or not decision.approved
        or getattr(getattr(plan, "scenario", None), "value", None) != "FAR"
    ):
        return decision
    method = str(getattr(plan, "details", {}).get("draw_method", ""))
    if method != "EXTERNAL_HAZARD_DOMINANCE":
        return replace(
            decision,
            approved=False,
            reason="FAR_REQUIRES_INDEPENDENT_EXTERNAL_DRAW",
        )
    return replace(
        decision,
        reason=f"INDEPENDENT_DRAW_{decision.reason}",
    )
