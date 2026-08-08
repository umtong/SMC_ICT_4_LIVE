from __future__ import annotations

from pathlib import Path
import json

root = Path(__file__).resolve().parent
logic_path = root / "logic.py"
text = logic_path.read_text(encoding="utf-8")
assert "Candidate 12 I15." in text

enum_anchor = (
    '    LONDON_LOW_ACCEPTANCE_REACCELERATION = '
    '"LONDON_LOW_ACCEPTANCE_REACCELERATION"\n'
)
assert text.count(enum_anchor) == 1, text.count(enum_anchor)
text = text.replace(
    enum_anchor,
    enum_anchor
    + '    ASIA_LOW_ACCEPTANCE_FAILURE_REVERSAL = '
    + '"ASIA_LOW_ACCEPTANCE_FAILURE_REVERSAL"\n'
    + '    LONDON_LOW_ACCEPTANCE_FAILURE_REVERSAL = '
    + '"LONDON_LOW_ACCEPTANCE_FAILURE_REVERSAL"\n',
    1,
)

state_anchor = (
    "    low_reacceptance_outside_closes: int = 0\n"
    "    low_reacceptance_done: bool = False\n"
)
assert text.count(state_anchor) == 1, text.count(state_anchor)
text = text.replace(
    state_anchor,
    state_anchor
    + "    low_failure_reversal_watch: bool = False\n"
    + "    low_failure_reversal_started_index: int | None = None\n"
    + "    low_failure_acceptance_trough: float | None = None\n"
    + "    low_failure_pullback_peak: float | None = None\n"
    + "    low_failure_secondary_sweep: float | None = None\n"
    + "    low_failure_scenario_id: str | None = None\n",
    1,
)

helper_anchor = (
    "    def _fresh_bear_fvg_general(self, bar: FiveBar) -> BearFVG | None:\n"
)
assert text.count(helper_anchor) == 1, text.count(helper_anchor)
bull_helper = '''    def _fresh_bull_fvg_general(self, bar: FiveBar) -> BullFVG | None:
        if len(self._bars) < 2 or len(self._bar_atrs) < 2:
            return None
        first = self._bars[-2]
        displacement = self._bars[-1]
        displacement_atr = self._bar_atrs[-1]
        if displacement_atr is None or displacement_atr <= 0:
            return None
        if not (
            bar.low > first.high
            and displacement.close > displacement.open
            and displacement.body / displacement_atr
            >= self.config.acceptance_displacement_body_atr
            and displacement.close_location
            >= self.config.acceptance_displacement_min_close_location
        ):
            return None
        return BullFVG(
            lower=first.high,
            upper=bar.low,
            formed_index=self._five_index,
            formed_ts_ns=bar.ts_ns,
            displacement_body_atr=displacement.body / displacement_atr,
            displacement_close_location=displacement.close_location,
        )

'''
text = text.replace(helper_anchor, bull_helper + helper_anchor, 1)

failure_capture_anchor = (
    "            state.low_continuation_started_index = None\n"
    "            sid = state.low_acceptance_scenario_id\n"
)
assert text.count(failure_capture_anchor) == 1, text.count(failure_capture_anchor)
text = text.replace(
    failure_capture_anchor,
    (
        "            state.low_continuation_started_index = None\n"
        "            mid_value_source = (\n"
        "                source.close_location\n"
        "                >= self.config.low_acceptance_discount_close_cutoff\n"
        "                and source.close_location\n"
        "                < self.config.low_acceptance_premium_close_cutoff\n"
        "            )\n"
        "            state.low_failure_reversal_watch = mid_value_source\n"
        "            state.low_failure_reversal_started_index = (\n"
        "                self._five_index if mid_value_source else None\n"
        "            )\n"
        "            state.low_failure_acceptance_trough = (\n"
        "                state.low_acceptance_trough if mid_value_source else None\n"
        "            )\n"
        "            state.low_failure_pullback_peak = (\n"
        "                bar.high if mid_value_source else None\n"
        "            )\n"
        "            state.low_failure_secondary_sweep = None\n"
        "            sid = state.low_acceptance_scenario_id\n"
    ),
    1,
)

sid_anchor = (
    "            if sid is None:\n"
    "                sid = self._next_scenario_id(source.label, \"LOW-ACCEPTANCE-FAILURE\")\n"
    "            self._emit(\n"
)
assert text.count(sid_anchor) == 1, text.count(sid_anchor)
text = text.replace(
    sid_anchor,
    (
        "            if sid is None:\n"
        "                sid = self._next_scenario_id(source.label, \"LOW-ACCEPTANCE-FAILURE\")\n"
        "            state.low_failure_scenario_id = (\n"
        "                sid if state.low_failure_reversal_watch else None\n"
        "            )\n"
        "            self._emit(\n"
    ),
    1,
)

wait_anchor = '        if state.low_acceptance_phase == "WAIT_REACCEPT":\n'
assert text.count(wait_anchor) == 1, text.count(wait_anchor)
reversal_block = '''        if state.low_acceptance_phase == "WAIT_REACCEPT":
            # A mid-value sell-side acceptance failure is unresolved until a
            # second sell-side sweep occurs. Only then may an independent
            # bullish MSS/FVG open a reversal auction to the opposite completed
            # boundary. The original failure bar is not an entry.
            if state.low_failure_reversal_watch:
                started = state.low_failure_reversal_started_index
                acceptance_trough = state.low_failure_acceptance_trough
                pullback_peak = state.low_failure_pullback_peak
                reversal_sid = state.low_failure_scenario_id
                if (
                    started is None
                    or acceptance_trough is None
                    or pullback_peak is None
                    or reversal_sid is None
                ):
                    state.low_failure_reversal_watch = False
                    self.skips[
                        "LOW_ACCEPTANCE_FAILURE_REVERSAL_MISSING_STRUCTURE"
                    ] += 1
                elif (
                    self._five_index - started
                    > self.config.acceptance_retest_expiry_bars
                ):
                    state.low_failure_reversal_watch = False
                    self.skips[
                        "LOW_ACCEPTANCE_FAILURE_REVERSAL_EXPIRED"
                    ] += 1
                elif bar.high >= source.high:
                    state.low_failure_reversal_watch = False
                    self.skips[
                        "LOW_ACCEPTANCE_FAILURE_REVERSAL_TARGET_PRECONSUMED"
                    ] += 1
                else:
                    if state.low_failure_secondary_sweep is None:
                        state.low_failure_pullback_peak = max(
                            pullback_peak,
                            bar.high,
                        )
                        if (
                            bar.low
                            <= acceptance_trough - self.config.price_increment
                        ):
                            state.low_failure_secondary_sweep = bar.low
                            self._emit(
                                scenario_id=reversal_sid,
                                event_type=(
                                    "LOW_ACCEPTANCE_FAILURE_SECONDARY_SWEEP"
                                ),
                                event_time_ns=bar.ts_ns,
                                observed_time_ns=bar.ts_ns,
                                next_state="WAIT_BULLISH_MSS_FVG",
                                reason_code=(
                                    "SECOND_SELL_SIDE_SWEEP_AFTER_FAILED_ACCEPTANCE"
                                ),
                                reference_price=acceptance_trough,
                                details={
                                    "source": source.label.value,
                                    "acceptance_trough": acceptance_trough,
                                    "secondary_sweep": bar.low,
                                    "frozen_failure_peak": (
                                        state.low_failure_pullback_peak
                                    ),
                                },
                            )
                        return None

                    state.low_failure_secondary_sweep = min(
                        state.low_failure_secondary_sweep,
                        bar.low,
                    )
                    fresh = self._fresh_bull_fvg_general(bar)
                    displacement = self._bars[-1] if self._bars else None
                    frozen_peak = state.low_failure_pullback_peak
                    confirmed = (
                        fresh is not None
                        and displacement is not None
                        and frozen_peak is not None
                        and displacement.close > source.low
                        and displacement.close > frozen_peak
                        and bar.close > source.low
                    )
                    if confirmed:
                        assert fresh is not None
                        assert frozen_peak is not None
                        sweep_extreme = state.low_failure_secondary_sweep
                        assert sweep_extreme is not None
                        scenario = (
                            ScenarioKind.ASIA_LOW_ACCEPTANCE_FAILURE_REVERSAL
                            if source.label is SessionLabel.ASIA
                            else ScenarioKind.LONDON_LOW_ACCEPTANCE_FAILURE_REVERSAL
                        )
                        structural_stop = (
                            sweep_extreme
                            - self.config.fvg_stop_buffer_atr * atr
                        )
                        plan = self._costed_plan(
                            scenario_id=reversal_sid,
                            scenario=scenario,
                            direction=Direction.LONG,
                            entry_order=EntryOrder.LIMIT_GTD,
                            observed_ts_ns=bar.ts_ns,
                            bar=bar,
                            atr=atr,
                            entry_raw=fresh.upper,
                            stop_raw=structural_stop,
                            target_raw=source.high,
                            expire_ts_ns=(
                                bar.ts_ns
                                + self.config.limit_entry_expiry_bars
                                * self.config.bar_minutes
                                * NS_MINUTE
                            ),
                            details={
                                "source": source.label.value,
                                "route": (
                                    "FAILED_LOW_ACCEPTANCE_SECONDARY_SWEEP_"
                                    "BULLISH_MSS_FVG"
                                ),
                                "session_high": source.high,
                                "session_low": source.low,
                                "session_width": source.width,
                                "source_close_location": source.close_location,
                                "acceptance_trough": acceptance_trough,
                                "failure_pullback_peak": frozen_peak,
                                "secondary_sweep": sweep_extreme,
                                "fresh_fvg_lower": fresh.lower,
                                "fresh_fvg_upper": fresh.upper,
                                "fresh_fvg_formed_ts_ns": fresh.formed_ts_ns,
                                "structural_invalidation": structural_stop,
                                "decision_body_atr": (
                                    fresh.displacement_body_atr
                                ),
                                "decision_close_location": (
                                    fresh.displacement_close_location
                                ),
                                "target_semantics": (
                                    "OPPOSITE_COMPLETED_SESSION_BOUNDARY"
                                ),
                            },
                        )
                        state.low_failure_reversal_watch = False
                        if plan is None:
                            self.skips[
                                "LOW_ACCEPTANCE_FAILURE_REVERSAL_COSTED_PLAN_REJECTED"
                            ] += 1
                            return None
                        self.scenario_counts[scenario.value] += 1
                        state.low_plan_emitted = True
                        state.trade_plan_emitted = True
                        return self._emit_plan(plan, allow_entry)
                    return None

'''
text = text.replace(wait_anchor, reversal_block, 1)
text = text.replace("Candidate 12 I15.", "Candidate 12 I16.", 1)
logic_path.write_text(text, encoding="utf-8")

tests_path = root / "test_logic.py"
tests = tests_path.read_text(encoding="utf-8")
marker = (
    "    def test_failed_premium_low_acceptance_can_reaccept_bearishly"
    "(self) -> None:\n"
)
assert tests.count(marker) == 1, tests.count(marker)
reversal_test = '''    def test_mid_value_failed_low_acceptance_can_reverse_after_secondary_sweep(
        self,
    ) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(
            config(max_stop_atr=100.0),
            "X",
        )
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 99, 101, 99, 100), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 100, 100, 91, 92), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 92, 96.1, 90, 93), True)
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 20), 93, 97, 92, 96),
                True,
            )
        )
        state = engine._sources[SessionLabel.ASIA]
        self.assertTrue(state.low_failure_reversal_watch)
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 25), 96, 96, 88.5, 89),
                True,
            )
        )
        self.assertAlmostEqual(state.low_failure_secondary_sweep or 0.0, 88.5)
        engine._on_five(
            bar(ts(y, m, d, 6, 30), 89, 99, 88.8, 98.5),
            True,
        )
        plan = engine._on_five(
            bar(ts(y, m, d, 6, 35), 98.5, 100, 96.5, 99),
            True,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(
            plan.scenario,
            ScenarioKind.ASIA_LOW_ACCEPTANCE_FAILURE_REVERSAL,
        )
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.entry_order, EntryOrder.LIMIT_GTD)
        self.assertEqual(
            plan.details["route"],
            "FAILED_LOW_ACCEPTANCE_SECONDARY_SWEEP_BULLISH_MSS_FVG",
        )
        self.assertAlmostEqual(plan.target_price, 105.0)

'''
tests = tests.replace(marker, reversal_test + marker, 1)
tests_path.write_text(tests, encoding="utf-8")

config_path = root / "config.json"
config = json.loads(config_path.read_text(encoding="utf-8"))
config["schema_version"] = 17
config["candidate"] = "candidate-12-two-stage-failure-router"
config["selection"]["selection_rule"] = (
    "W1-W12 are exposed evidence. I16 preserves I15 and adds a separate "
    "mid-value failed sell-side acceptance reversal. The first close back "
    "inside is never an entry. A second sell-side sweep must occur, then a "
    "bullish displacement must break the frozen failure peak and create a "
    "fresh FVG. Entry is a one-bar protected FVG limit, invalidation is "
    "beyond the second sweep, and target is the opposite completed boundary. "
    "Only existing discount/premium cutoffs, displacement, cost, and risk "
    "gates are used."
)
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
