from __future__ import annotations

from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "research" / "candidate-12"

RESEARCH_WORKFLOW = """name: candidate-12-i13-awaiting-authoritative-gate\n\non:\n  workflow_dispatch:\n\npermissions:\n  contents: read\n\njobs:\n  staged:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo I13 source installed; authoritative gate is installed next\n"""



def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    logic_path = CANDIDATE / "logic.py"
    logic = logic_path.read_text(encoding="utf-8")
    plan_anchor = (
        "            prior_peak = state.acceptance_peak or source.high\n"
        "            plan = self._costed_plan(\n"
    )
    market_block = '''            prior_peak = state.acceptance_peak or source.high

            # Fresh reacceleration is already a completed confirmation event.
            # Enter at that close when the close itself still preserves the
            # configured costed structural R. Otherwise retain the protected
            # one-bar FVG limit. Both branches use the existing min_net_r.
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
    logic = replace_once(logic, plan_anchor, market_block, "market-plan anchor")
    detail_anchor = '''                    "initial_pullback_low": state.acceptance_pullback_low,
                    "prior_acceptance_peak": prior_peak,
                    "target_semantics": "PRIOR_ACCEPTANCE_EXPANSION_HIGH",
'''
    detail_replacement = '''                    "initial_pullback_low": state.acceptance_pullback_low,
                    "prior_acceptance_peak": prior_peak,
                    "decision_close": bar.close,
                    "execution_semantics": (
                        "MARKET_CLOSE_FAILED_COSTED_R_THEN_ONE_BAR_PROTECTED_LIMIT"
                    ),
                    "target_semantics": "PRIOR_ACCEPTANCE_EXPANSION_HIGH",
'''
    logic = replace_once(logic, detail_anchor, detail_replacement, "limit details")
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
    end = tests.index(end_H@‘