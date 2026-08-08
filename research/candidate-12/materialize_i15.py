from __future__ import annotations

from pathlib import Path
import json

root = Path(__file__).resolve().parent
logic_path = root / "logic.py"
text = logic_path.read_text(encoding="utf-8")
assert "Candidate 12 I14b." in text

enum_anchor = '    LONDON_HIGH_ACCEPTANCE = "LONDON_HIGH_ACCEPTANCE"\n'
assert text.count(enum_anchor) == 1, text.count(enum_anchor)
text = text.replace(
    enum_anchor,
    enum_anchor
    + '    ASIA_HIGH_ACCEPTANCE_FAILURE = "ASIA_HIGH_ACCEPTANCE_FAILURE"\n'
    + '    LONDON_HIGH_ACCEPTANCE_FAILURE = "LONDON_HIGH_ACCEPTANCE_FAILURE"\n',
    1,
)

state_anchor = (
    "    reacceptance_anchor_fvg: BullFVG | None = None\n"
    "    reacceptance_done: bool = False\n\n"
    "    outside_low_closes: int = 0\n"
)
assert text.count(state_anchor) == 1, text.count(state_anchor)
text = text.replace(
    state_anchor,
    (
        "    reacceptance_anchor_fvg: BullFVG | None = None\n"
        "    reacceptance_done: bool = False\n"
        "    high_failure_watch: bool = False\n"
        "    high_failure_fvg: BullFVG | None = None\n"
        "    high_failure_peak: float | None = None\n"
        "    high_failure_bar_low: float | None = None\n"
        "    high_failure_started_index: int | None = None\n"
        "    high_failure_scenario_id: str | None = None\n\n"
        "    outside_low_closes: int = 0\n"
    ),
    1,
)

method_start = text.index("    def _advance_high_acceptance(\n")
comment_pos = text.index(
    "        # A completed close back inside terminates the current acceptance and\n",
    method_start,
)
prefix = text[method_start:comment_pos]
assert "        if state.high_plan_emitted:\n            return None\n" in prefix
failure_block = '''        # A deep failure is a separate auction only when the
        # original accepted imbalance has been destroyed, no trade has
        # already been emitted from this completed boundary, and bearish
        # displacement breaks the failure leg within the existing reclaim
        # horizon. This is not a second entry on the same episode.
        if state.high_failure_watch:
            started = state.high_failure_started_index
            failed_fvg = state.high_failure_fvg
            failure_peak = state.high_failure_peak
            failure_low = state.high_failure_bar_low
            failure_sid = state.high_failure_scenario_id
            if (
                started is None
                or failed_fvg is None
                or failure_peak is None
                or failure_low is None
                or failure_sid is None
            ):
                state.high_failure_watch = False
                self.skips["HIGH_ACCEPTANCE_FAILURE_MISSING_STRUCTURE"] += 1
            elif self._five_index - started > self.config.reclaim_max_bars:
                state.high_failure_watch = False
                self.skips["HIGH_ACCEPTANCE_FAILURE_CONFIRMATION_EXPIRED"] += 1
            elif bar.close > source.high:
                state.high_failure_watch = False
                self.skips["HIGH_ACCEPTANCE_FAILURE_REACCEPTED"] += 1
            else:
                confirmed = (
                    bar.close < bar.open
                    and bar.body / atr
                    >= self.config.acceptance_displacement_body_atr
                    and bar.close_location
                    <= self.config.low_acceptance_displacement_max_close_location
                    and bar.close < failure_low
                    and bar.close < failed_fvg.lower
                )
                if confirmed:
                    scenario = (
                        ScenarioKind.ASIA_HIGH_ACCEPTANCE_FAILURE
                        if source.label is SessionLabel.ASIA
                        else ScenarioKind.LONDON_HIGH_ACCEPTANCE_FAILURE
                    )
                    structural_stop = (
                        failure_peak + self.config.fvg_stop_buffer_atr * atr
                    )
                    plan = self._costed_plan(
                        scenario_id=failure_sid,
                        scenario=scenario,
                        direction=Direction.SHORT,
                        entry_order=EntryOrder.MARKET,
                        observed_ts_ns=bar.ts_ns,
                        bar=bar,
                        atr=atr,
                        entry_raw=bar.close,
                        stop_raw=structural_stop,
                        target_raw=source.low,
                        expire_ts_ns=None,
                        details={
                            "source": source.label.value,
                            "route": "DEEP_HIGH_ACCEPTANCE_FAILURE_BEARISH_MSS",
                            "session_high": source.high,
                            "session_low": source.low,
                            "session_width": source.width,
                            "failed_fvg_lower": failed_fvg.lower,
                            "failed_fvg_upper": failed_fvg.upper,
                            "failure_peak": failure_peak,
                            "failure_leg_low": failure_low,
                            "structural_invalidation": structural_stop,
                            "confirmation_body_atr": bar.body / atr,
                            "confirmation_close_location": bar.close_location,
                            "target_semantics": "OPPOSITE_COMPLETED_SESSION_BOUNDARY",
                        },
                    )
                    state.high_failure_watch = False
                    if plan is None:
                        self.skips[
                            "HIGH_ACCEPTANCE_FAILURE_COSTED_PLAN_REJECTED"
                        ] += 1
                        return None
                    self.scenario_counts[scenario.value] += 1
                    state.high_plan_emitted = True
                    state.trade_plan_emitted = True
                    return self._emit_plan(plan, allow_entry)

'''
text = text[:comment_pos] + failure_block + text[comment_pos:]

capture_anchor = "                original_fvg = state.active_fvg\n"
assert text.count(capture_anchor) == 1, text.count(capture_anchor)
text = text.replace(
    capture_anchor,
    capture_anchor
    + "                failure_peak = max(\n"
    + "                    state.acceptance_peak or source.high, bar.high\n"
    + "                )\n",
    1,
)

reset_anchor = (
    "                state.acceptance_pullback_low = None\n"
    "                state.acceptance_peak = None\n"
    "                if not reacceptance_eligible:\n"
)
assert text.count(reset_anchor) == 1, text.count(reset_anchor)
text = text.replace(
    reset_anchor,
    (
        "                state.acceptance_pullback_low = None\n"
        "                state.acceptance_peak = None\n"
        "                state.high_failure_watch = (\n"
        "                    original_fvg is not None\n"
        "                    and not reacceptance_eligible\n"
        "                )\n"
        "                state.high_failure_fvg = (\n"
        "                    original_fvg if state.high_failure_watch else None\n"
        "                )\n"
        "                state.high_failure_peak = (\n"
        "                    failure_peak if state.high_failure_watch else None\n"
        "                )\n"
        "                state.high_failure_bar_low = (\n"
        "                    bar.low if state.high_failure_watch else None\n"
        "                )\n"
        "                state.high_failure_started_index = (\n"
        "                    self._five_index if state.high_failure_watch else None\n"
        "                )\n"
        "                if not reacceptance_eligible:\n"
    ),
    1,
)

sid_anchor = (
    '                sid = self._next_scenario_id('
    'source.label, "HIGH-ACCEPTANCE-FAILURE")\n'
)
assert text.count(sid_anchor) == 1, text.count(sid_anchor)
text = text.replace(
    sid_anchor,
    sid_anchor
    + "                state.high_failure_scenario_id = (\n"
    + "                    sid if state.high_failure_watch else None\n"
    + "                )\n",
    1,
)

text = text.replace("Candidate 12 I14b.", "Candidate 12 I15.", 1)
logic_path.write_text(text, encoding="utf-8")

tests_path = root / "test_logic.py"
tests = tests_path.read_text(encoding="utf-8")
marker = (
    "    def test_opposite_boundaries_have_independent_causal_lifecycles"
    "(self) -> None:\n"
)
assert tests.count(marker) == 1, tests.count(marker)
failure_test = '''    def test_deep_high_acceptance_failure_requires_bearish_mss(self) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(
            config(max_stop_atr=100.0),
            "X",
        )
        self.seed_asia(engine)
        self.form_asia_high_acceptance(engine)
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 20), 108, 108.2, 105.2, 105.7),
                True,
            )
        )
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 25), 105.7, 106, 103.5, 103.8),
                True,
            )
        )
        state = engine._sources[SessionLabel.ASIA]
        self.assertTrue(state.high_failure_watch)
        plan = engine._on_five(
            bar(ts(y, m, d, 6, 30), 103.8, 104, 97, 98),
            True,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(
            plan.scenario,
            ScenarioKind.ASIA_HIGH_ACCEPTANCE_FAILURE,
        )
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)
        self.assertEqual(
            plan.details["route"],
            "DEEP_HIGH_ACCEPTANCE_FAILURE_BEARISH_MSS",
        )
        self.assertAlmostEqual(plan.target_price, 95.0)

'''
tests = tests.replace(marker, failure_test + marker, 1)
tests_path.write_text(tests, encoding="utf-8")

config_path = root / "config.json"
config = json.loads(config_path.read_text(encoding="utf-8"))
config["schema_version"] = 16
config["candidate"] = "candidate-12-deep-acceptance-failure-router"
config["selection"]["selection_rule"] = (
    "W1-W12 are exposed evidence. I15 preserves I14b and adds one "
    "distinct failed-auction route. After accepted high-side price "
    "closes back inside and destroys the original FVG, bearish "
    "displacement must break the failure leg inside the existing "
    "reclaim horizon. Stop is beyond the failed-auction peak and "
    "target is the opposite completed boundary. A boundary which "
    "already emitted a trade cannot emit this route."
)
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
