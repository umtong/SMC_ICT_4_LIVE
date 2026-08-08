from __future__ import annotations

from pathlib import Path
import json

root = Path(__file__).resolve().parent
logic_path = root / "logic.py"
text = logic_path.read_text(encoding="utf-8")
assert "Candidate 12 I17." in text

state_anchor = (
    "    high_failure_started_index: int | None = None\n"
    "    high_failure_scenario_id: str | None = None\n\n"
    "    outside_low_closes: int = 0\n"
)
assert text.count(state_anchor) == 1, text.count(state_anchor)
text = text.replace(
    state_anchor,
    (
        "    high_failure_started_index: int | None = None\n"
        "    high_failure_scenario_id: str | None = None\n"
        "    high_rearm_watch: bool = False\n"
        "    high_rearm_entry: float | None = None\n"
        "    high_rearm_target: float | None = None\n"
        "    high_rearm_started_index: int | None = None\n"
        "    high_rearm_mitigation_low: float | None = None\n"
        "    high_rearm_scenario_id: str | None = None\n\n"
        "    outside_low_closes: int = 0\n"
    ),
    1,
)

advance_start = text.index("    def _advance_high_acceptance(\n")
source_pos = text.index("        source = state.source\n", advance_start)
insert_pos = source_pos + len("        source = state.source\n")
rearm_branch = '''        if state.high_rearm_watch:
            rearm_entry = state.high_rearm_entry
            rearm_target = state.high_rearm_target
            rearm_started = state.high_rearm_started_index
            rearm_sid = state.high_rearm_scenario_id
            if (
                rearm_entry is None
                or rearm_target is None
                or rearm_started is None
                or rearm_sid is None
            ):
                state.high_rearm_watch = False
                self.skips["HIGH_REARM_MISSING_STRUCTURE"] += 1
                return None
            if (
                self._five_index - rearm_started
                > self.config.acceptance_retest_expiry_bars
            ):
                state.high_rearm_watch = False
                self.skips["HIGH_REARM_EXPIRED"] += 1
                return None
            if bar.high >= rearm_target:
                state.high_rearm_watch = False
                self.skips["HIGH_REARM_TARGET_PRECONSUMED"] += 1
                return None
            if bar.close <= source.high:
                state.high_rearm_watch = False
                self.skips["HIGH_REARM_COMPLETED_BOUNDARY_LOST"] += 1
                return None

            mitigation_low = state.high_rearm_mitigation_low
            if mitigation_low is None:
                if bar.low <= rearm_entry:
                    state.high_rearm_mitigation_low = bar.low
                    self._emit(
                        scenario_id=rearm_sid,
                        event_type="UNFILLED_REACCELERATION_LATER_MITIGATION",
                        event_time_ns=bar.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        next_state="WAIT_ACTIVE_HOLD",
                        reason_code=(
                            "PRICE_RETURNED_TO_EXPIRED_PROTECTED_ENTRY_AFTER_MOVING_AWAY"
                        ),
                        reference_price=rearm_entry,
                        details={
                            "source": source.label.value,
                            "expired_entry": rearm_entry,
                            "mitigation_low": bar.low,
                            "frozen_target": rearm_target,
                        },
                    )
                return None

            state.high_rearm_mitigation_low = min(mitigation_low, bar.low)
            active_hold = (
                bar.close > bar.open
                and bar.body / atr >= self.config.active_retest_body_atr
                and bar.close_location
                >= self.config.active_retest_min_close_location
                and bar.close > rearm_entry
                and bar.low >= source.high
            )
            if not active_hold:
                return None

            structural_stop = (
                state.high_rearm_mitigation_low
                - self.config.fvg_stop_buffer_atr * atr
            )
            scenario = (
                ScenarioKind.ASIA_HIGH_ACCEPTANCE
                if source.label is SessionLabel.ASIA
                else ScenarioKind.LONDON_HIGH_ACCEPTANCE
            )
            plan = self._costed_plan(
                scenario_id=rearm_sid,
                scenario=scenario,
                direction=Direction.LONG,
                entry_order=EntryOrder.MARKET,
                observed_ts_ns=bar.ts_ns,
                bar=bar,
                atr=atr,
                entry_raw=bar.close,
                stop_raw=structural_stop,
                target_raw=rearm_target,
                expire_ts_ns=None,
                details={
                    "source": source.label.value,
                    "route": (
                        "UNFILLED_REACCELERATION_LATER_MITIGATION_ACTIVE_HOLD"
                    ),
                    "session_high": source.high,
                    "session_low": source.low,
                    "session_width": source.width,
                    "expired_protected_entry": rearm_entry,
                    "later_mitigation_low": state.high_rearm_mitigation_low,
                    "structural_invalidation": structural_stop,
                    "decision_body_atr": bar.body / atr,
                    "decision_close_location": bar.close_location,
                    "target_semantics": "FROZEN_PRIOR_ACCEPTANCE_EXPANSION_HIGH",
                },
            )
            state.high_rearm_watch = False
            if plan is None:
                self.skips["HIGH_REARM_COSTED_PLAN_REJECTED"] += 1
                return None
            self.scenario_counts[scenario.value] += 1
            state.high_plan_emitted = True
            state.trade_plan_emitted = True
            return self._emit_plan(plan, allow_entry)

'''
text = text[:insert_pos] + rearm_branch + text[insert_pos:]

mark_anchor = "    def mark_plan_rejected(\n"
assert text.count(mark_anchor) == 1, text.count(mark_anchor)
rearm_method = '''    def rearm_unfilled_plan(self, plan: TradePlan, ts_ns: int) -> bool:
        if (
            plan.entry_order is not EntryOrder.LIMIT_GTD
            or plan.details.get("route")
            != "FVG_BREACH_HELD_BOUNDARY_THEN_FRESH_REACCELERATION_LIMIT"
        ):
            return False
        raw_source = plan.details.get("source")
        try:
            label = SessionLabel(str(raw_source))
        except ValueError:
            self.skips["HIGH_REARM_UNKNOWN_SOURCE"] += 1
            return False
        state = self._sources.get(label)
        if state is None:
            self.skips["HIGH_REARM_SOURCE_NOT_LIVE"] += 1
            return False
        state.high_plan_emitted = False
        state.trade_plan_emitted = False
        state.high_rearm_watch = True
        state.high_rearm_entry = plan.expected_entry
        state.high_rearm_target = plan.target_price
        state.high_rearm_started_index = self._five_index
        state.high_rearm_mitigation_low = None
        state.high_rearm_scenario_id = self._next_scenario_id(
            label,
            "HIGH-ACCEPTANCE-UNFILLED-REARM",
        )
        state.acceptance_phase = "WAIT_UNFILLED_REARM"
        self._emit(
            scenario_id=state.high_rearm_scenario_id,
            event_type="UNFILLED_PROTECTED_ENTRY_REARMED",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            next_state="WAIT_LATER_MITIGATION",
            reason_code=(
                "EXPIRED_UNFILLED_AFTER_PRICE_MOVED_AWAY_REQUIRES_NEW_MITIGATION_HOLD"
            ),
            reference_price=plan.expected_entry,
            details={
                "source": label.value,
                "prior_scenario_id": plan.scenario_id,
                "expired_entry": plan.expected_entry,
                "frozen_target": plan.target_price,
                "prior_stop": plan.stop_price,
            },
        )
        return True

'''
text = text.replace(mark_anchor, rearm_method + mark_anchor, 1)
text = text.replace("Candidate 12 I17.", "Candidate 12 I18.", 1)
logic_path.write_text(text, encoding="utf-8")

adapter_path = root / "strategy_adapter.py"
adapter = adapter_path.read_text(encoding="utf-8")
old_terminal = '''        def _terminal_if_flat(self, ts_ns: int, reason: str) -> None:
            if self.active_plan is not None and self._slot_free():
                self.logic.mark_trade_terminal(
                    self.active_plan,
                    ts_ns,
                    reason,
                    {"lifecycle_events": len(self.lifecycle)},
                )
                self.active_plan = None
'''
assert adapter.count(old_terminal) == 1, adapter.count(old_terminal)
new_terminal = '''        def _terminal_if_flat(self, ts_ns: int, reason: str) -> None:
            if self.active_plan is not None and self._slot_free():
                terminal_plan = self.active_plan
                self.logic.mark_trade_terminal(
                    terminal_plan,
                    ts_ns,
                    reason,
                    {"lifecycle_events": len(self.lifecycle)},
                )
                if reason == "GTD_ENTRY_EXPIRED_UNFILLED":
                    self.logic.rearm_unfilled_plan(terminal_plan, ts_ns)
                self.active_plan = None
'''
adapter = adapter.replace(old_terminal, new_terminal, 1)
adapter = adapter.replace(
    '"""NautilusTrader adapter for Candidate 12 I7 causal market/limit plans."""',
    '"""NautilusTrader adapter for Candidate 12 I18 causal market/limit plans."""',
    1,
)
adapter_path.write_text(adapter, encoding="utf-8")

tests_path = root / "test_logic.py"
tests = tests_path.read_text(encoding="utf-8")
marker = (
    "    def test_fresh_reacceleration_uses_market_when_close_retains_costed_r(\n"
)
assert tests.count(marker) == 1, tests.count(marker)
rearm_test = '''    def test_expired_reacceleration_limit_requires_later_mitigation_and_active_hold(
        self,
    ) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(
            config(min_net_r=1.5, max_stop_atr=100.0),
            "X",
        )
        self.seed_asia(engine)
        engine._on_five(bar(ts(y, m, d, 6, 5), 103, 104, 102, 103), True)
        engine._on_five(bar(ts(y, m, d, 6, 10), 103, 112, 103, 110), True)
        engine._on_five(bar(ts(y, m, d, 6, 15), 110, 115, 105.5, 110), True)
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 20), 108, 108.2, 103.5, 105.2),
                True,
            )
        )
        engine._on_five(bar(ts(y, m, d, 6, 25), 105.2, 106, 104.8, 105.5), True)
        engine._on_five(bar(ts(y, m, d, 6, 30), 105.5, 109, 105.4, 108.5), True)
        expired = engine._on_five(
            bar(ts(y, m, d, 6, 35), 108.5, 110, 106.5, 109),
            True,
        )
        self.assertIsNotNone(expired)
        assert expired is not None
        self.assertEqual(expired.entry_order, EntryOrder.LIMIT_GTD)
        self.assertTrue(
            engine.rearm_unfilled_plan(
                expired,
                ts(y, m, d, 6, 40),
            )
        )
        state = engine._sources[SessionLabel.ASIA]
        self.assertTrue(state.high_rearm_watch)
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 6, 45), 109, 109.2, 107.0, 107.2),
                True,
            )
        )
        plan = engine._on_five(
            bar(ts(y, m, d, 6, 50), 107.2, 110.5, 107.1, 110.2),
            True,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)
        self.assertEqual(
            plan.details["route"],
            "UNFILLED_REACCELERATION_LATER_MITIGATION_ACTIVE_HOLD",
        )
        self.assertAlmostEqual(plan.target_price, 115.0)

'''
tests = tests.replace(marker, rearm_test + marker, 1)
tests_path.write_text(tests, encoding="utf-8")

config_path = root / "config.json"
config = json.loads(config_path.read_text(encoding="utf-8"))
config["schema_version"] = 19
config["candidate"] = "candidate-12-unfilled-rearm-auction-router"
config["selection"]["selection_rule"] = (
    "W1-W12 are exposed evidence. I18 preserves I17 and repairs one execution "
    "state rather than lengthening GTD. A protected reacceleration limit which "
    "expires because price moved away may be rearmed once only after price later "
    "returns to that frozen entry and a completed bullish active hold forms while "
    "the completed boundary and frozen target remain valid. The new mitigation "
    "low becomes invalidation; existing active-hold, costed min_net_r, risk, and "
    "target-consumption gates remain authoritative."
)
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
