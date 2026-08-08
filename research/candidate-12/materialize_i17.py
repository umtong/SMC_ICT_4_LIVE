from __future__ import annotations

from pathlib import Path
import json

root = Path(__file__).resolve().parent
logic_path = root / "logic.py"
text = logic_path.read_text(encoding="utf-8")
assert "Candidate 12 I16." in text

state_anchor = (
    "    low_failure_pullback_peak: float | None = None\n"
    "    low_failure_secondary_sweep: float | None = None\n"
    "    low_failure_scenario_id: str | None = None\n"
)
assert text.count(state_anchor) == 1, text.count(state_anchor)
text = text.replace(
    state_anchor,
    (
        "    low_failure_pullback_peak: float | None = None\n"
        "    low_failure_secondary_sweep: float | None = None\n"
        "    low_failure_mss_peak: float | None = None\n"
        "    low_failure_scenario_id: str | None = None\n"
    ),
    1,
)

capture_anchor = (
    "            state.low_failure_secondary_sweep = None\n"
    "            sid = state.low_acceptance_scenario_id\n"
)
assert text.count(capture_anchor) == 1, text.count(capture_anchor)
text = text.replace(
    capture_anchor,
    (
        "            state.low_failure_secondary_sweep = None\n"
        "            state.low_failure_mss_peak = None\n"
        "            sid = state.low_acceptance_scenario_id\n"
    ),
    1,
)

wait_start = text.index('        if state.low_acceptance_phase == "WAIT_REACCEPT":\n')
watch_start = text.index(
    "            if state.low_failure_reversal_watch:\n",
    wait_start,
)
premium_guard = text.index(
    "            if (\n                source.label is not SessionLabel.ASIA\n",
    watch_start,
)
new_watch = '''            if state.low_failure_reversal_watch:
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
                    current_sweep = state.low_failure_secondary_sweep
                    if current_sweep is None:
                        if (
                            bar.low
                            <= acceptance_trough - self.config.price_increment
                        ):
                            state.low_failure_secondary_sweep = bar.low
                            state.low_failure_pullback_peak = bar.high
                            state.low_failure_mss_peak = None
                            self._emit(
                                scenario_id=reversal_sid,
                                event_type=(
                                    "LOW_ACCEPTANCE_FAILURE_FIRST_POST_FAILURE_SWEEP"
                                ),
                                event_time_ns=bar.ts_ns,
                                observed_time_ns=bar.ts_ns,
                                next_state="WAIT_RE_SWEEP",
                                reason_code=(
                                    "FIRST_SELL_SIDE_SWEEP_AFTER_FAILED_ACCEPTANCE"
                                ),
                                reference_price=acceptance_trough,
                                details={
                                    "source": source.label.value,
                                    "acceptance_trough": acceptance_trough,
                                    "first_post_failure_sweep": bar.low,
                                },
                            )
                        return None

                    # A deeper re-sweep freezes only the intervening local
                    # pullback high. The later MSS must break that high, so
                    # entry, invalidation, and confirmation belong to the new
                    # auction leg rather than to the original acceptance leg.
                    if bar.low <= current_sweep - self.config.price_increment:
                        state.low_failure_mss_peak = pullback_peak
                        state.low_failure_secondary_sweep = bar.low
                        state.low_failure_pullback_peak = bar.high
                        self._emit(
                            scenario_id=reversal_sid,
                            event_type="LOW_ACCEPTANCE_FAILURE_RE_SWEEP",
                            event_time_ns=bar.ts_ns,
                            observed_time_ns=bar.ts_ns,
                            next_state="WAIT_LOCAL_BULLISH_MSS_FVG",
                            reason_code=(
                                "DEEPER_SELL_SIDE_RE_SWEEP_FROZE_INTERVENING_HIGH"
                            ),
                            reference_price=current_sweep,
                            details={
                                "source": source.label.value,
                                "prior_sweep": current_sweep,
                                "deeper_re_sweep": bar.low,
                                "frozen_local_mss_peak": pullback_peak,
                            },
                        )
                        return None

                    state.low_failure_pullback_peak = max(
                        pullback_peak,
                        bar.high,
                    )
                    fresh = self._fresh_bull_fvg_general(bar)
                    displacement = self._bars[-1] if self._bars else None
                    local_mss_peak = state.low_failure_mss_peak
                    confirmed = (
                        fresh is not None
                        and displacement is not None
                        and local_mss_peak is not None
                        and displacement.close > source.low
                        and displacement.close > local_mss_peak
                        and bar.close > source.low
                    )
                    if confirmed:
                        assert fresh is not None
                        assert local_mss_peak is not None
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
                        common_details = {
                            "source": source.label.value,
                            "session_high": source.high,
                            "session_low": source.low,
                            "session_width": source.width,
                            "source_close_location": source.close_location,
                            "acceptance_trough": acceptance_trough,
                            "local_mss_peak": local_mss_peak,
                            "secondary_sweep": sweep_extreme,
                            "fresh_fvg_lower": fresh.lower,
                            "fresh_fvg_upper": fresh.upper,
                            "fresh_fvg_formed_ts_ns": fresh.formed_ts_ns,
                            "structural_invalidation": structural_stop,
                            "decision_body_atr": fresh.displacement_body_atr,
                            "decision_close_location": (
                                fresh.displacement_close_location
                            ),
                            "target_semantics": (
                                "OPPOSITE_COMPLETED_SESSION_BOUNDARY"
                            ),
                        }
                        market_plan = self._costed_plan(
                            scenario_id=reversal_sid,
                            scenario=scenario,
                            direction=Direction.LONG,
                            entry_order=EntryOrder.MARKET,
                            observed_ts_ns=bar.ts_ns,
                            bar=bar,
                            atr=atr,
                            entry_raw=bar.close,
                            stop_raw=structural_stop,
                            target_raw=source.high,
                            expire_ts_ns=None,
                            details={
                                **common_details,
                                "route": (
                                    "FAILED_LOW_ACCEPTANCE_RE_SWEEP_"
                                    "BULLISH_MSS_MARKET"
                                ),
                                "execution_semantics": (
                                    "COMPLETED_LOCAL_MSS_CLOSE_RETAINED_COSTED_R"
                                ),
                            },
                        )
                        if market_plan is not None:
                            state.low_failure_reversal_watch = False
                            self.scenario_counts[scenario.value] += 1
                            state.low_plan_emitted = True
                            state.trade_plan_emitted = True
                            return self._emit_plan(market_plan, allow_entry)

                        limit_plan = self._costed_plan(
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
                                **common_details,
                                "route": (
                                    "FAILED_LOW_ACCEPTANCE_RE_SWEEP_"
                                    "BULLISH_MSS_LIMIT"
                                ),
                                "execution_semantics": (
                                    "MARKET_CLOSE_FAILED_COSTED_R_THEN_"
                                    "ONE_BAR_PROTECTED_FVG_LIMIT"
                                ),
                            },
                        )
                        state.low_failure_reversal_watch = False
                        if limit_plan is None:
                            self.skips[
                                "LOW_ACCEPTANCE_FAILURE_REVERSAL_COSTED_PLAN_REJECTED"
                            ] += 1
                            return None
                        self.scenario_counts[scenario.value] += 1
                        state.low_plan_emitted = True
                        state.trade_plan_emitted = True
                        return self._emit_plan(limit_plan, allow_entry)
                    return None

'''
text = text[:watch_start] + new_watch + text[premium_guard:]
text = text.replace("Candidate 12 I16.", "Candidate 12 I17.", 1)
logic_path.write_text(text, encoding="utf-8")

tests_path = root / "test_logic.py"
tests = tests_path.read_text(encoding="utf-8")
test_start = tests.index(
    "    def test_mid_value_failed_low_acceptance_can_reverse_after_secondary_sweep(\n"
)
test_end = tests.index(
    "    def test_failed_premium_low_acceptance_can_reaccept_bearishly",
    test_start,
)
replacement_test = '''    def test_mid_value_failed_low_acceptance_requires_re_sweep_and_local_mss(
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
        # Intervening pullback high belongs to the local auction.
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 30), 89, 96, 89, 94),
                True,
            )
        )
        # A deeper re-sweep freezes that intervening high as the MSS level.
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 35), 94, 95, 87, 88),
                True,
            )
        )
        self.assertAlmostEqual(state.low_failure_mss_peak or 0.0, 96.0)
        engine._on_five(
            bar(ts(y, m, d, 6, 40), 88, 99, 87.5, 98.5),
            True,
        )
        plan = engine._on_five(
            bar(ts(y, m, d, 6, 45), 98.5, 100, 95.5, 99),
            True,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(
            plan.scenario,
            ScenarioKind.ASIA_LOW_ACCEPTANCE_FAILURE_REVERSAL,
        )
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)
        self.assertEqual(
            plan.details["route"],
            "FAILED_LOW_ACCEPTANCE_RE_SWEEP_BULLISH_MSS_MARKET",
        )
        self.assertAlmostEqual(plan.target_price, 105.0)

'''
tests = tests[:test_start] + replacement_test + tests[test_end:]
tests_path.write_text(tests, encoding="utf-8")

config_path = root / "config.json"
config = json.loads(config_path.read_text(encoding="utf-8"))
config["schema_version"] = 18
config["candidate"] = "candidate-12-local-mss-failure-router"
config["selection"]["selection_rule"] = (
    "W1-W12 are exposed evidence. I17 corrects the I16 MSS frame without "
    "adding a threshold. After failed sell-side acceptance, the first "
    "post-failure sweep is followed by an intervening pullback. A deeper "
    "re-sweep freezes only that local pullback high; bullish displacement "
    "must break it and create a fresh FVG. The existing costed min_net_r "
    "selects completed-close market entry, one-bar protected limit, or no "
    "trade. Invalidation remains beyond the deeper re-sweep and target is "
    "the opposite completed boundary."
)
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
