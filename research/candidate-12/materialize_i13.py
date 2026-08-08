from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "research" / "candidate-12"

PLACEHOLDER_WORKFLOW = """name: candidate-12-i13-awaiting-authoritative-gate

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  staged:
    runs-on: ubuntu-24.04
    steps:
      - run: echo I13 source installed; authoritative gate is installed next
"""


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    logic_path = CANDIDATE / "logic.py"
    logic = logic_path.read_text(encoding="utf-8")
    anchor = (
        "            prior_peak = state.acceptance_peak or source.high\n"
        "            plan = self._costed_plan(\n"
    )
    market_block = '''            prior_peak = state.acceptance_peak or source.high

            # A fresh FVG after the first mitigation failure is completed
            # reacceleration. Enter at the close only when that close still
            # passes the existing costed min_net_r gate; otherwise retain the
            # protected one-bar FVG limit.
            market_plan = self._costed_plan(
                scenario_id=sid,
                scenario=(
                    ScenarioKind.ASIA_HIGH_ACCEPTANCE
                    if source.label is SessionLabel.ASIA
                    else ScenarioKind.LONDON_HIGH_ACCEPTANCE
                ),
                direction=Direction.LONG,
                entry_order=EntryOrder.MARKET,
                observed_ts_ns=bar.ts_ns,
                bar=bar,
                atr=atr,
                entry_raw=bar.close,
                stop_raw=fresh.lower - self.config.fvg_stop_buffer_atr * atr,
                target_raw=prior_peak,
                expire_ts_ns=None,
                details={
                    "source": source.label.value,
                    "route": "FVG_BREACH_HELD_BOUNDARY_THEN_FRESH_REACCELERATION_MARKET",
                    "session_high": source.high,
                    "session_low": source.low,
                    "session_width": source.width,
                    "fvg_lower": fresh.lower,
                    "fvg_upper": fresh.upper,
                    "fvg_formed_ts_ns": fresh.formed_ts_ns,
                    "initial_pullback_low": state.acceptance_pullback_low,
                    "prior_acceptance_peak": prior_peak,
                    "decision_close": bar.close,
                    "execution_semantics": (
                        "COMPLETED_FRESH_REACCELERATION_CLOSE_RETAINED_COSTED_R"
                    ),
                    "target_semantics": "PRIOR_ACCEPTANCE_EXPANSION_HIGH",
                },
            )
            if market_plan is not None:
                state.high_plan_emitted = True
                state.trade_plan_emitted = True
                return self._emit_plan(market_plan, allow_entry)

            plan = self._costed_plan(
'''
    logic = replace_once(logic, anchor, market_block, "market anchor")
    old_details = '''                    "initial_pullback_low": state.acceptance_pullback_low,
                    "prior_acceptance_peak": prior_peak,
                    "target_semantics": "PRIOR_ACCEPTANCE_EXPANSION_HIGH",
'''
    new_details = '''                    "initial_pullback_low": state.acceptance_pullback_low,
                    "prior_acceptance_peak": prior_peak,
                    "decision_close": bar.close,
                    "execution_semantics": (
                        "MARKET_CLOSE_FAILED_COSTED_R_THEN_ONE_BAR_PROTECTED_LIMIT"
                    ),
                    "target_semantics": "PRIOR_ACCEPTANCE_EXPANSION_HIGH",
'''
    logic = replace_once(logic, old_details, new_details, "limit details")
    logic = replace_once(
        logic,
        '"""Causal completed-session auction router for Candidate 12 I12.',
        '"""Causal completed-session auction router for Candidate 12 I13.',
        "module title",
    )
    logic_path.write_text(logic, encoding="utf-8")

    tests_path = CANDIDATE / "test_logic.py"
    tests = tests_path.read_text(encoding="utf-8")
    start_marker = (
        "    def test_fvg_lower_edge_breach_waits_for_fresh_reacceleration_limit"
        "(self) -> None:\n"
    )
    end_marker = "    def test_opposite_boundaries_have_independent_causal_lifecycles"
    if tests.count(start_marker) != 1:
        raise RuntimeError("limit test marker is not unique")
    start = tests.index(start_marker)
    end = tests.index(end_marker, start)
    limit_test = tests[start:end]
    limit_test = replace_once(
        limit_test,
        start_marker,
        "    def test_fresh_reacceleration_falls_back_to_limit_when_market_r_is_insufficient(\n"
        "        self,\n"
        "    ) -> None:\n",
        "limit test name",
    )
    limit_test = replace_once(
        limit_test,
        '        engine = CausalLiquidityAuctionEngine(config(), "X")\n',
        '        engine = CausalLiquidityAuctionEngine(config(min_net_r=3.0), "X")\n',
        "limit test config",
    )
    market_test = '''    def test_fresh_reacceleration_uses_market_when_close_retains_costed_r(
        self,
    ) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(config(min_net_r=1.0), "X")
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 103, 104, 102, 103), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 103, 112, 103, 110), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 110, 115, 105.5, 110), True)
        self.assertIsNone(
            engine._on_five(bar(ts(y, m, d, 6, 20), 108, 108.2, 103.5, 105.2), True)
        )
        engine._on_five(bar(ts(y, m, d, 6, 25), 105.2, 106, 104.8, 105.5), True)
        engine._on_five(bar(ts(y, m, d, 6, 30), 105.5, 109, 105.4, 108.5), True)
        plan = engine._on_five(
            bar(ts(y, m, d, 6, 35), 108.5, 110, 106.5, 109), True
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)
        self.assertEqual(
            plan.details["route"],
            "FVG_BREACH_HELD_BOUNDARY_THEN_FRESH_REACCELERATION_MARKET",
        )
        self.assertAlmostEqual(plan.expected_entry, 109.0)
        self.assertAlmostEqual(plan.target_price, 115.0)
        self.assertGreaterEqual(plan.net_r, 1.0)

'''
    tests = tests[:start] + limit_test + market_test + tests[end:]
    tests_path.write_text(tests, encoding="utf-8")

    config_path = CANDIDATE / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["schema_version"] != 12:
        raise RuntimeError(f"unexpected schema {config['schema_version']}")
    expected = "candidate-12-confirmed-extension-session-auction-router"
    if config["candidate"] != expected:
        raise RuntimeError(f"unexpected candidate {config['candidate']}")
    config["schema_version"] = 13
    config["candidate"] = "candidate-12-state-priced-reacceleration-router"
    config["selection"]["selection_rule"] = (
        "W1-W11 are exposed evidence. I13 repairs the W2 high-acceptance execution miss "
        "without new data or a fitted parameter. After the initial bullish FVG is breached "
        "while the completed session high remains accepted, a fresh bullish FVG is completed "
        "reacceleration. Enter its completed close only when the existing costed min_net_r "
        "gate still passes to the frozen prior acceptance peak; otherwise preserve the one-bar "
        "protected FVG limit. Sell-side failed-pullback routes keep limit-only execution. I13 "
        "must convert W2 into a Nautilus market trade, preserve the W5 protected-limit winner, "
        "and re-pass all prior causal, cost, risk, slot, event-log, and liquidation invariants."
    )
    config["selection"]["implementation_week"] = "W2"
    config["selection"]["diagnosis_week"] = "W2"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    workflow_path = ROOT / ".github" / "workflows" / "candidate-12-research.yml"
    workflow_path.write_text(PLACEHOLDER_WORKFLOW, encoding="utf-8")


if __name__ == "__main__":
    main()
