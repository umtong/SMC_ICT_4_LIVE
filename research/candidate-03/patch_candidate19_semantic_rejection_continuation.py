#!/usr/bin/env python3
"""Arm a continuation auction when cross-market semantics reject a local FAR.

Candidate 18 showed that a completed failed-boundary retest without synchronized
peer acceptance is not sufficient. Candidate 19 therefore keeps Candidate 17's
full acceptance -> defended retest -> reacceleration confirmation, but broadens
where that state may begin. It starts not only after a real Nautilus FAR stop,
but also after a fully formed local FAR plan is rejected before entry for one of
two directionally meaningful market-semantic reasons:

* peers do not provide the dominant reclaim needed by the proposed reversal; or
* the reversal remains inside a severe unresolved adverse auction.

The rejection is not inverted immediately. It only freezes the failed sweep
boundary and starts a new causal watch. A trade still requires two closes beyond
the boundary, a defended retest, local reacceleration, the unchanged AAC
cross-market gate, a strict pre-existing external target, after-cost 1.25R, the
exact 3% current-NAV loss budget, and the one global slot.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD_BLOCK = '''def candidate16_mark_rejected(\n    self: CausalAuctionEngine,\n    plan: TradePlan,\n    ts_ns: int,\n    reason: str,\n    details: dict[str, Any] | None = None,\n) -> None:\n    if plan.details.get("scenario_kind") == SCENARIO_KIND:\n        state: FailedFarState | None = getattr(self, "_candidate16_failed_far_state", None)\n        if state is None or state.scenario_id != plan.scenario_id:\n            return\n        self._event(\n            plan.scenario_id,\n            "ENTRY_PLAN_REJECTED",\n            plan.observed_ts_ns,\n            ts_ns,\n            state.state,\n            "TERMINAL",\n            reason,\n            plan.expected_entry,\n            details or {},\n        )\n        self.skips[reason] += 1\n        self._candidate16_failed_far_state = None\n        return\n    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)\n\n\n'''

NEW_BLOCK = '''SEMANTIC_REJECTED_FAR_REASONS = frozenset(\n    {\n        "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM",\n        "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION",\n    }\n)\n\n\ndef semantic_rejected_far_context(\n    plan: TradePlan,\n    reason: str,\n) -> SubmittedFarContext | None:\n    \"\"\"Freeze only complete FAR plans rejected for directional market state.\"\"\"\n    if plan.scenario != Scenario.FAR or reason not in SEMANTIC_REJECTED_FAR_REASONS:\n        return None\n    details = plan.details or {}\n    required = ("sweep_extreme", "pool_level", "pool_source", "sweep_ts_ns")\n    if any(details.get(key) is None for key in required):\n        return None\n    pool_side = Side.HIGH if plan.direction == Direction.SHORT else Side.LOW\n    boundary = float(details["sweep_extreme"])\n    return SubmittedFarContext(\n        parent_scenario_id=plan.scenario_id,\n        pool_side=pool_side,\n        pool_level=float(details["pool_level"]),\n        pool_source=str(details["pool_source"]),\n        source_strength=int(details.get("source_strength", 1)),\n        boundary=boundary,\n        original_direction=plan.direction,\n        original_entry=float(plan.expected_entry),\n        original_stop=float(plan.stop_price),\n        original_target=float(plan.target_price),\n        atr=float(plan.atr),\n        sweep_ts_ns=int(details["sweep_ts_ns"]),\n        stop_model=str(details.get("stop_model", "UNSUBMITTED_FAR")),\n    )\n\n\ndef arm_semantic_rejected_far(\n    self: CausalAuctionEngine,\n    plan: TradePlan,\n    ts_ns: int,\n    reason: str,\n    decision_details: dict[str, Any] | None,\n    context: SubmittedFarContext,\n) -> None:\n    \"\"\"Start a watch; never submit or size an order at the rejection event.\"\"\"\n    if getattr(self, "_candidate16_failed_far_state", None) is not None:\n        self.skips["SEMANTIC_REJECTED_FAR_STATE_ALREADY_ACTIVE"] += 1\n        return\n    if not self.bars:\n        self.skips["SEMANTIC_REJECTED_FAR_WITHOUT_REFERENCE_BAR"] += 1\n        return\n    reference = float(self.bars[-1].close)\n    target = strict_failed_far_target(self, context, reference)\n    scenario_id = f"{context.parent_scenario_id}-REJECTED-FAR-{ts_ns}"\n    if target is None:\n        self.skips["SEMANTIC_REJECTED_FAR_NO_STRICT_EXTERNAL_TARGET"] += 1\n        self._event(\n            scenario_id,\n            "SEMANTIC_REJECTED_FAR_CONTINUATION_TERMINAL",\n            context.sweep_ts_ns,\n            ts_ns,\n            "PLAN_REJECTED",\n            "TERMINAL",\n            "SEMANTIC_REJECTED_FAR_NO_STRICT_EXTERNAL_TARGET",\n            context.boundary,\n            {"semantic_rejection": reason, **(decision_details or {})},\n        )\n        return\n    direction = continuation_direction(context.pool_side)\n    target_ahead = target.level > reference if direction == Direction.LONG else target.level < reference\n    if not target_ahead:\n        self.skips["SEMANTIC_REJECTED_FAR_TARGET_NOT_AHEAD"] += 1\n        return\n    state = FailedFarState(\n        scenario_id=scenario_id,\n        parent_scenario_id=context.parent_scenario_id,\n        side=context.pool_side,\n        direction=direction,\n        boundary=context.boundary,\n        target_pool_id=target.scenario_id,\n        target_price=target.level,\n        source_pool_level=context.pool_level,\n        source_pool_source=context.pool_source,\n        source_strength=context.source_strength,\n        failure_ts_ns=ts_ns,\n        failure_index=self._index,\n        expiry_index=self._index + self.config.event_expiry_bars,\n        original_entry=context.original_entry,\n        original_stop=context.original_stop,\n        original_target=context.original_target,\n    )\n    self._candidate16_failed_far_state = state\n    self._event(\n        scenario_id,\n        "SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED",\n        context.sweep_ts_ns,\n        ts_ns,\n        "PLAN_REJECTED",\n        "WAIT_ACCEPTANCE",\n        reason,\n        context.boundary,\n        {\n            "parent_scenario_id": context.parent_scenario_id,\n            "continuation_direction": direction.value,\n            "failed_boundary": context.boundary,\n            "target_pool": target.scenario_id,\n            "target": target.level,\n            "original_far_direction": context.original_direction.value,\n            "original_far_entry": context.original_entry,\n            "original_far_stop": context.original_stop,\n            "original_far_target": context.original_target,\n            "semantic_rejection": reason,\n            **(decision_details or {}),\n        },\n    )\n\n\ndef candidate16_mark_rejected(\n    self: CausalAuctionEngine,\n    plan: TradePlan,\n    ts_ns: int,\n    reason: str,\n    details: dict[str, Any] | None = None,\n) -> None:\n    if plan.details.get("scenario_kind") == SCENARIO_KIND:\n        state: FailedFarState | None = getattr(self, "_candidate16_failed_far_state", None)\n        if state is None or state.scenario_id != plan.scenario_id:\n            return\n        self._event(\n            plan.scenario_id,\n            "ENTRY_PLAN_REJECTED",\n            plan.observed_ts_ns,\n            ts_ns,\n            state.state,\n            "TERMINAL",\n            reason,\n            plan.expected_entry,\n            details or {},\n        )\n        self.skips[reason] += 1\n        self._candidate16_failed_far_state = None\n        return\n\n    context = semantic_rejected_far_context(plan, reason)\n    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)\n    if context is not None:\n        arm_semantic_rejected_far(self, plan, ts_ns, reason, details, context)\n\n\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "def semantic_rejected_far_context(" in source:
        return False
    if "def strict_failed_far_target(" not in source:
        raise RuntimeError("Candidate 17 strict target must be installed first")
    if source.count(OLD_BLOCK) != 1:
        raise RuntimeError(f"expected one mark_rejected block, found {source.count(OLD_BLOCK)}")
    source = source.replace(OLD_BLOCK, NEW_BLOCK, 1)
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate19 semantic-rejection continuation patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
