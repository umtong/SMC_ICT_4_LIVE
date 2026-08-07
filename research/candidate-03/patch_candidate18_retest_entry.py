#!/usr/bin/env python3
"""Enter a failed-FAR continuation when its completed boundary retest defends.

Candidate 17 observed four completed acceptance + boundary-defense sequences, but
three reached their predeclared external objective while waiting for a second
reacceleration confirmation. Candidate 18 removes only that duplicate terminal
confirmation. The causal sequence remains: real FAR stop -> two closes beyond
the failed boundary -> completed boundary retest closing back outside -> next
native quote market entry. Cross-market AAC semantics, strict external target,
fees, 3% NAV loss budget and global slot remain unchanged.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD_KIND = 'SCENARIO_KIND = "FAILED_FAR_STRICT_EXTERNAL_ACCEPTANCE_CONTINUATION"\n'
NEW_KIND = 'SCENARIO_KIND = "FAILED_FAR_STRICT_TARGET_RETEST_CONTINUATION"\n'

OLD_BLOCK = '''    if state.state == "WAIT_RETEST":\n        if state.direction == Direction.LONG:\n            state.acceptance_impulse_extreme = max(\n                float(state.acceptance_impulse_extreme),\n                bar.high,\n            )\n        else:\n            state.acceptance_impulse_extreme = min(\n                float(state.acceptance_impulse_extreme),\n                bar.low,\n            )\n        if defended_boundary_retest(\n            state,\n            bar,\n            atr,\n            hold_atr=self.config.acceptance_hold_atr,\n            retest_atr=self.config.acceptance_retest_atr,\n        ):\n            state.state = "WAIT_REACCELERATION"\n            state.retest_ts_ns = bar.ts_ns\n            state.retest_extreme = bar.low if state.direction == Direction.LONG else bar.high\n            state.reacceleration_level = float(state.acceptance_impulse_extreme)\n            self._event(\n                state.scenario_id,\n                "FAILED_FAR_BOUNDARY_RETEST_DEFENDED",\n                state.failure_ts_ns,\n                bar.ts_ns,\n                "WAIT_RETEST",\n                "WAIT_REACCELERATION",\n                "FAILED_SWEEP_BOUNDARY_RETEST_CLOSED_OUTSIDE",\n                state.boundary,\n                {\n                    "retest_extreme": state.retest_extreme,\n                    "reacceleration_level": state.reacceleration_level,\n                },\n            )\n        return None\n'''

NEW_BLOCK = '''    if state.state == "WAIT_RETEST":\n        if state.direction == Direction.LONG:\n            state.acceptance_impulse_extreme = max(\n                float(state.acceptance_impulse_extreme),\n                bar.high,\n            )\n        else:\n            state.acceptance_impulse_extreme = min(\n                float(state.acceptance_impulse_extreme),\n                bar.low,\n            )\n        if not defended_boundary_retest(\n            state,\n            bar,\n            atr,\n            hold_atr=self.config.acceptance_hold_atr,\n            retest_atr=self.config.acceptance_retest_atr,\n        ):\n            return None\n\n        state.retest_ts_ns = bar.ts_ns\n        state.retest_extreme = bar.low if state.direction == Direction.LONG else bar.high\n        if state.direction == Direction.LONG:\n            stop = min(float(state.retest_extreme), state.boundary) - self.config.stop_buffer_atr * atr\n        else:\n            stop = max(float(state.retest_extreme), state.boundary) + self.config.stop_buffer_atr * atr\n        entry = bar.close\n        risk, loss, net_gain, net_r = market_economics(\n            direction=state.direction,\n            entry=entry,\n            stop=stop,\n            target=state.target_price,\n            taker_rate=self.config.effective_taker_rate,\n            target_maker_rate=self.config.effective_maker_rate,\n        )\n        causal_order = (\n            stop < entry < state.target_price\n            if state.direction == Direction.LONG\n            else state.target_price < entry < stop\n        )\n        if (\n            not causal_order\n            or risk <= 0.0\n            or risk / atr < self.config.min_stop_atr\n            or net_gain <= 0.0\n            or net_r < self.config.min_net_r\n        ):\n            _terminal(self, state, bar, "FAILED_FAR_RETEST_ENTRY_INSUFFICIENT_COSTED_R")\n            return None\n\n        self._event(\n            state.scenario_id,\n            "FAILED_FAR_BOUNDARY_RETEST_DEFENDED",\n            state.failure_ts_ns,\n            bar.ts_ns,\n            "WAIT_RETEST",\n            "RETEST_CONFIRMED",\n            "FAILED_SWEEP_BOUNDARY_RETEST_CLOSED_OUTSIDE",\n            state.boundary,\n            {\n                "retest_extreme": state.retest_extreme,\n                "strict_external_target": state.target_price,\n            },\n        )\n        plan = TradePlan(\n            scenario_id=state.scenario_id,\n            scenario=Scenario.AAC,\n            direction=state.direction,\n            observed_ts_ns=bar.ts_ns,\n            expected_entry=entry,\n            stop_price=stop,\n            target_price=state.target_price,\n            atr=atr,\n            loss_per_unit=loss,\n            gain_per_unit=net_gain,\n            net_r=net_r,\n            reason_code="FAILED_FAR_ACCEPTANCE_BOUNDARY_RETEST_MARKET",\n            expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,\n            entry_order_type="MARKET",\n            entry_post_only=False,\n            details={\n                "scenario_kind": SCENARIO_KIND,\n                "parent_scenario_id": state.parent_scenario_id,\n                "failure_ts_ns": state.failure_ts_ns,\n                "sweep_ts_ns": state.failure_ts_ns,\n                "failed_boundary": state.boundary,\n                "source_pool_level": state.source_pool_level,\n                "source_pool_source": state.source_pool_source,\n                "source_strength": state.source_strength,\n                "acceptance_ts_ns": state.acceptance_ts_ns,\n                "retest_ts_ns": state.retest_ts_ns,\n                "retest_extreme": state.retest_extreme,\n                "target_pool": state.target_pool_id,\n                "entry_model": "FAILED_BOUNDARY_COMPLETED_RETEST_MARKET",\n                "stop_model": "FAILED_BOUNDARY_REACCEPTANCE",\n                "entry_cost_assumption": "TAKER",\n                "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,\n                "original_far_entry": state.original_entry,\n                "original_far_stop": state.original_stop,\n                "original_far_target": state.original_target,\n            },\n        )\n        self._event(\n            state.scenario_id,\n            "TRADE_PLAN_CONFIRMED",\n            state.failure_ts_ns,\n            bar.ts_ns,\n            "RETEST_CONFIRMED",\n            "PLAN_CONFIRMED",\n            plan.reason_code,\n            entry,\n            {\n                "scenario": Scenario.AAC.value,\n                "scenario_kind": SCENARIO_KIND,\n                "direction": state.direction.value,\n                "entry_order_type": "MARKET",\n                "entry_post_only": False,\n                "target": state.target_price,\n                "stop": stop,\n                "net_r": net_r,\n            },\n        )\n        state.state = "PLAN_CONFIRMED"\n        return plan\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "FAILED_FAR_ACCEPTANCE_BOUNDARY_RETEST_MARKET" in source:
        return False
    if source.count(OLD_KIND) != 1:
        raise RuntimeError(f"expected one Candidate 17 kind, found {source.count(OLD_KIND)}")
    if source.count(OLD_BLOCK) != 1:
        raise RuntimeError(f"expected one WAIT_RETEST block, found {source.count(OLD_BLOCK)}")
    source = source.replace(OLD_KIND, NEW_KIND, 1)
    source = source.replace(OLD_BLOCK, NEW_BLOCK, 1)
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate18 retest-entry patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
