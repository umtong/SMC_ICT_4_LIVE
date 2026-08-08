#!/usr/bin/env python3
"""Enter a semantic-rejected FAR continuation when two-close acceptance completes.

Candidate 20 established that pre-entry semantic-rejection watches can progress
causally: 12 of 50 reached two completed closes beyond the failed sweep
boundary. Requiring price to return to that exact boundary and then accelerate
again produced only two defended retests and no entries. For this origin, the
second outside close is itself the completed auction decision:

    complete local FAR rejected by cross-market state
      -> price invalidates the local reclaim
      -> two completed closes accept beyond the swept boundary
      -> submit a new AAC continuation candidate

The plan is not automatically traded. It still passes the unchanged AAC
cross-market semantic gate in the portfolio runner. Entry is a native market
order after the completed second close; invalidation is reacceptance through the
failed boundary plus the existing ATR buffer; target is the already-frozen
strict external pool; after-cost 1.25R, exact 3% current-NAV risk and one global
slot are unchanged. Post-stop failed-FAR states retain the full retest and
reacceleration sequence.
"""
from __future__ import annotations

import argparse
from pathlib import Path

STEP_FUNCTION_ANCHOR = '''def _step(self: CausalAuctionEngine, bar: BarObs) -> TradePlan | None:\n'''

HELPER = '''def _semantic_two_close_acceptance_plan(\n    self: CausalAuctionEngine,\n    state: FailedFarState,\n    bar: BarObs,\n    atr: float,\n) -> TradePlan | None:\n    \"\"\"Build a costed plan only after the inside-origin acceptance completes.\"\"\"\n    if state.origin_kind != "SEMANTIC_REJECTION":\n        raise RuntimeError("two-close entry is restricted to semantic-rejection origin")\n    if state.direction == Direction.LONG:\n        stop = state.boundary - self.config.stop_buffer_atr * atr\n    else:\n        stop = state.boundary + self.config.stop_buffer_atr * atr\n    entry = bar.close\n    risk, loss, net_gain, net_r = market_economics(\n        direction=state.direction,\n        entry=entry,\n        stop=stop,\n        target=state.target_price,\n        taker_rate=self.config.effective_taker_rate,\n        target_maker_rate=self.config.effective_maker_rate,\n    )\n    causal_order = (\n        stop < entry < state.target_price\n        if state.direction == Direction.LONG\n        else state.target_price < entry < stop\n    )\n    if (\n        not causal_order\n        or risk <= 0.0\n        or risk / atr < self.config.min_stop_atr\n        or net_gain <= 0.0\n        or net_r < self.config.min_net_r\n    ):\n        _terminal(\n            self,\n            state,\n            bar,\n            "SEMANTIC_REJECTED_FAR_ACCEPTANCE_INSUFFICIENT_COSTED_R",\n        )\n        return None\n\n    plan = TradePlan(\n        scenario_id=state.scenario_id,\n        scenario=Scenario.AAC,\n        direction=state.direction,\n        observed_ts_ns=bar.ts_ns,\n        expected_entry=entry,\n        stop_price=stop,\n        target_price=state.target_price,\n        atr=atr,\n        loss_per_unit=loss,\n        gain_per_unit=net_gain,\n        net_r=net_r,\n        reason_code="SEMANTIC_REJECTED_FAR_TWO_CLOSE_ACCEPTANCE_MARKET",\n        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,\n        entry_order_type="MARKET",\n        entry_post_only=False,\n        details={\n            "scenario_kind": SCENARIO_KIND,\n            "origin_kind": state.origin_kind,\n            "parent_scenario_id": state.parent_scenario_id,\n            "failure_ts_ns": state.failure_ts_ns,\n            "sweep_ts_ns": state.failure_ts_ns,\n            "failed_boundary": state.boundary,\n            "source_pool_level": state.source_pool_level,\n            "source_pool_source": state.source_pool_source,\n            "source_strength": state.source_strength,\n            "acceptance_ts_ns": state.acceptance_ts_ns,\n            "acceptance_impulse_extreme": state.acceptance_impulse_extreme,\n            "outside_closes": state.outside_streak,\n            "target_pool": state.target_pool_id,\n            "entry_model": "TWO_CLOSE_FAILED_BOUNDARY_ACCEPTANCE_MARKET",\n            "stop_model": "FAILED_BOUNDARY_REACCEPTANCE",\n            "entry_cost_assumption": "TAKER",\n            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,\n            "original_far_entry": state.original_entry,\n            "original_far_stop": state.original_stop,\n            "original_far_target": state.original_target,\n        },\n    )\n    self._event(\n        state.scenario_id,\n        "TRADE_PLAN_CONFIRMED",\n        state.failure_ts_ns,\n        bar.ts_ns,\n        "ACCEPTANCE_CONFIRMED",\n        "PLAN_CONFIRMED",\n        plan.reason_code,\n        entry,\n        {\n            "scenario": Scenario.AAC.value,\n            "scenario_kind": SCENARIO_KIND,\n            "origin_kind": state.origin_kind,\n            "direction": state.direction.value,\n            "entry_order_type": "MARKET",\n            "entry_post_only": False,\n            "target": state.target_price,\n            "stop": stop,\n            "net_r": net_r,\n        },\n    )\n    state.state = "PLAN_CONFIRMED"\n    return plan\n\n\n'''

ACCEPTANCE_BLOCK = '''        if state.outside_streak >= self.config.acceptance_min_closes:\n            state.state = "WAIT_RETEST"\n            state.acceptance_ts_ns = bar.ts_ns\n            self._event(\n                state.scenario_id,\n                "FAILED_FAR_ACCEPTANCE_CONFIRMED",\n                state.failure_ts_ns,\n                bar.ts_ns,\n                "WAIT_ACCEPTANCE",\n                "WAIT_RETEST",\n                "TWO_CLOSES_BEYOND_FAILED_SWEEP_BOUNDARY",\n                state.boundary,\n                {\n                    "direction": state.direction.value,\n                    "outside_closes": state.outside_streak,\n                    "acceptance_impulse_extreme": state.acceptance_impulse_extreme,\n                },\n            )\n        return None\n'''

ACCEPTANCE_REPLACEMENT = '''        if state.outside_streak >= self.config.acceptance_min_closes:\n            semantic_origin = state.origin_kind == "SEMANTIC_REJECTION"\n            state.state = "ACCEPTANCE_CONFIRMED" if semantic_origin else "WAIT_RETEST"\n            state.acceptance_ts_ns = bar.ts_ns\n            self._event(\n                state.scenario_id,\n                "FAILED_FAR_ACCEPTANCE_CONFIRMED",\n                state.failure_ts_ns,\n                bar.ts_ns,\n                "WAIT_ACCEPTANCE",\n                state.state,\n                "TWO_CLOSES_BEYOND_FAILED_SWEEP_BOUNDARY",\n                state.boundary,\n                {\n                    "direction": state.direction.value,\n                    "origin_kind": state.origin_kind,\n                    "outside_closes": state.outside_streak,\n                    "acceptance_impulse_extreme": state.acceptance_impulse_extreme,\n                },\n            )\n            if semantic_origin:\n                return _semantic_two_close_acceptance_plan(self, state, bar, atr)\n        return None\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "def _semantic_two_close_acceptance_plan(" in source:
        return False
    if "def deep_reentry_is_terminal(" not in source:
        raise RuntimeError("Candidate 20 inside-origin patch must be installed first")
    if source.count(STEP_FUNCTION_ANCHOR) != 1:
        raise RuntimeError("expected exactly one _step anchor")
    if source.count(ACCEPTANCE_BLOCK) != 1:
        raise RuntimeError(
            f"expected one acceptance block, found {source.count(ACCEPTANCE_BLOCK)}"
        )
    source = source.replace(STEP_FUNCTION_ANCHOR, HELPER + STEP_FUNCTION_ANCHOR, 1)
    source = source.replace(ACCEPTANCE_BLOCK, ACCEPTANCE_REPLACEMENT, 1)
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate21 two-close acceptance patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
