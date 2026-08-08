from __future__ import annotations
from pathlib import Path
import json

root = Path(__file__).resolve().parent
lp = root / "logic.py"
s = lp.read_text(encoding="utf-8")
assert "Candidate 12 I18." in s

enum = '    LONDON_LOW_REJECTION = "LONDON_LOW_REJECTION"\n'
assert s.count(enum) == 1
s = s.replace(enum, enum + '    LONDON_LOW_DELAYED_REJECTION = "LONDON_LOW_DELAYED_REJECTION"\n', 1)

wait = '''        if episode.phase == "WAIT_RECLAIM":
            episode.extreme = min(episode.extreme, bar.low)
            if bar.close > source.low:
                self._mark_raid_reclaim(episode, bar, atr)
                return None
            if self._five_index - episode.sweep_index >= self.config.reclaim_max_bars:
                self._invalidate_raid(state, "LOW", bar, "LOW_BOUNDARY_ACCEPTED_NOT_RECLAIMED")
            return None

'''
assert s.count(wait) == 1
delayed = wait + '''        if episode.phase == "WAIT_DELAYED_BULL_FVG":
            assert episode.reclaim_index is not None
            assert episode.reclaim_bar is not None
            assert episode.atr_at_reclaim is not None
            reclaim = episode.reclaim_bar
            reclaim_atr = episode.atr_at_reclaim
            hard_stop = (
                episode.extreme
                - self.config.rejection_stop_buffer_atr * reclaim_atr
            )
            if bar.low <= hard_stop or bar.close < source.low:
                self._invalidate_raid(
                    state, "LOW", bar, "DELAYED_LOW_REJECTION_INVALIDATED"
                )
                return None
            if (
                self._five_index - episode.reclaim_index
                > self.config.delayed_rejection_expiry_bars
            ):
                self._invalidate_raid(
                    state, "LOW", bar, "DELAYED_LOW_REJECTION_EXPIRED"
                )
                return None
            fresh = self._fresh_bull_fvg_general(bar)
            displacement = self._bars[-1] if self._bars else None
            if fresh is None or displacement is None:
                return None
            if not (
                displacement.close > reclaim.high
                and displacement.close > source.low
                and bar.close > source.low
            ):
                return None
            boundary_distance = (fresh.lower - source.low) / atr
            if (
                fresh.lower
                < source.low - self.config.fvg_boundary_tolerance_atr * atr
                or boundary_distance
                > self.config.delayed_rejection_fvg_max_boundary_distance_atr
            ):
                self.skips["DELAYED_LOW_REJECTION_FVG_NOT_NEAR_SOURCE"] += 1
                return None
            local_stop = (
                min(displacement.low, bar.low)
                - self.config.fvg_stop_buffer_atr * atr
            )
            plan = self._costed_plan(
                scenario_id=episode.scenario_id,
                scenario=ScenarioKind.LONDON_LOW_DELAYED_REJECTION,
                direction=Direction.LONG,
                entry_order=EntryOrder.MARKET,
                observed_ts_ns=bar.ts_ns,
                bar=bar,
                atr=atr,
                entry_raw=bar.close,
                stop_raw=local_stop,
                target_raw=source.high,
                expire_ts_ns=None,
                details={
                    "source": source.label.value,
                    "route": "DELAYED_SELL_SIDE_FAILED_AUCTION_LOCAL_BULLISH_MSS_FVG",
                    "session_high": source.high,
                    "session_low": source.low,
                    "session_width": source.width,
                    "raid_extreme": episode.extreme,
                    "sweep_ts_ns": episode.sweep_ts_ns,
                    "reclaim_ts_ns": episode.reclaim_ts_ns,
                    "reclaim_high": reclaim.high,
                    "fvg_lower": fresh.lower,
                    "fvg_upper": fresh.upper,
                    "fvg_formed_ts_ns": fresh.formed_ts_ns,
                    "local_displacement_low": displacement.low,
                    "structural_invalidation": local_stop,
                    "boundary_distance_atr": boundary_distance,
                    "decision_body_atr": fresh.displacement_body_atr,
                    "decision_close_location": fresh.displacement_close_location,
                    "target_semantics": "OPPOSITE_COMPLETED_SESSION_BOUNDARY",
                },
            )
            state.low_rejection = None
            state.low_rejection_done = True
            if plan is None:
                self.skips["DELAYED_LOW_REJECTION_COSTED_PLAN_REJECTED"] += 1
                return None
            self.scenario_counts[ScenarioKind.LONDON_LOW_DELAYED_REJECTION.value] += 1
            state.low_plan_emitted = True
            state.trade_plan_emitted = True
            return self._emit_plan(plan, allow_entry)

'''
s = s.replace(wait, delayed, 1)

weak = '''        if not reclaimed:
            self._invalidate_raid(state, "LOW", bar, "LOW_RECLAIM_LACKED_BULLISH_DISPLACEMENT")
            return None
'''
assert s.count(weak) == 1
weak2 = '''        if not reclaimed:
            if source.label is SessionLabel.LONDON:
                episode.phase = "WAIT_DELAYED_BULL_FVG"
                self.skips["LONDON_LOW_REJECTION_IMMEDIATE_RECLAIM_WEAK"] += 1
                self._emit(
                    scenario_id=episode.scenario_id,
                    event_type="IMMEDIATE_LOW_REJECTION_CONFIRMATION_ABSENT",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="WAIT_DELAYED_BULL_FVG",
                    reason_code="WEAK_RECLAIM_REQUIRES_LOCAL_BULLISH_MSS_FVG",
                    reference_price=source.low,
                    details={
                        "source": source.label.value,
                        "reclaim_body_atr": reclaim.body / reclaim_atr,
                        "reclaim_close_location": reclaim.close_location,
                        "delayed_expiry_bars": self.config.delayed_rejection_expiry_bars,
                    },
                )
                return None
            self._invalidate_raid(
                state, "LOW", bar, "LOW_RECLAIM_LACKED_BULLISH_DISPLACEMENT"
            )
            return None
'''
s = s.replace(weak, weak2, 1)

mss = '''        if not confirmed:
            self._invalidate_raid(state, "LOW", bar, "LOW_REJECTION_LACKED_BULLISH_MSS")
            return None
'''
assert s.count(mss) == 1
mss2 = '''        if not confirmed:
            if source.label is SessionLabel.LONDON:
                episode.phase = "WAIT_DELAYED_BULL_FVG"
                self.skips["LONDON_LOW_REJECTION_IMMEDIATE_MSS_ABSENT"] += 1
                self._emit(
                    scenario_id=episode.scenario_id,
                    event_type="IMMEDIATE_LOW_REJECTION_CONFIRMATION_ABSENT",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="WAIT_DELAYED_BULL_FVG",
                    reason_code="RECLAIM_HELD_BUT_LOCAL_BULLISH_MSS_FVG_REQUIRED",
                    reference_price=source.low,
                    details={
                        "source": source.label.value,
                        "reclaim_high": reclaim.high,
                        "decision_close": bar.close,
                        "decision_body_atr": bar.body / atr,
                        "delayed_expiry_bars": self.config.delayed_rejection_expiry_bars,
                    },
                )
                return None
            self._invalidate_raid(
                state, "LOW", bar, "LOW_REJECTION_LACKED_BULLISH_MSS"
            )
            return None
'''
s = s.replace(mss, mss2, 1)
s = s.replace("Candidate 12 I18.", "Candidate 12 I19.", 1)
lp.write_text(s, encoding="utf-8")

tp = root / "test_logic.py"
t = tp.read_text(encoding="utf-8")
marker = "    def test_low_acceptance_requires_failed_pullback_near_completed_boundary(self) -> None:\n"
assert t.count(marker) == 1
test = '''    def test_london_low_weak_reclaim_waits_for_local_bullish_mss_fvg(
        self,
    ) -> None:
        y, m, d = self.DAY
        engine = CausalLiquidityAuctionEngine(
            config(max_stop_atr=100.0),
            "X",
        )
        self.seed_london(engine)
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 12, 5), 95.2, 96, 90, 95.5),
                True,
            )
        )
        self.assertIsNone(
            engine._on_five(
                bar(ts(y, m, d, 12, 10), 95.5, 96, 94.5, 95.1),
                True,
            )
        )
        state = engine._sources[SessionLabel.LONDON]
        assert state.low_rejection is not None
        self.assertEqual(state.low_rejection.phase, "WAIT_DELAYED_BULL_FVG")
        engine._on_five(
            bar(ts(y, m, d, 12, 15), 95.1, 96, 94.8, 95.2),
            True,
        )
        engine._on_five(
            bar(ts(y, m, d, 12, 20), 95.2, 101, 95, 100.5),
            True,
        )
        plan = engine._on_five(
            bar(ts(y, m, d, 12, 25), 100.5, 102, 96.2, 100.8),
            True,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, ScenarioKind.LONDON_LOW_DELAYED_REJECTION)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.entry_order, EntryOrder.MARKET)
        self.assertEqual(
            plan.details["route"],
            "DELAYED_SELL_SIDE_FAILED_AUCTION_LOCAL_BULLISH_MSS_FVG",
        )
        self.assertAlmostEqual(plan.target_price, 105.0)
        self.assertGreater(plan.stop_price, 90.0)

'''
t = t.replace(marker, test + marker, 1)
tp.write_text(t, encoding="utf-8")

cp = root / "config.json"
c = json.loads(cp.read_text(encoding="utf-8"))
c["schema_version"] = 20
c["candidate"] = "candidate-12-delayed-low-failed-auction-router"
c["selection"]["selection_rule"] = (
    "W1-W12 are exposed evidence. I19 preserves I18 and adds one London-only "
    "delayed sell-side failed auction. A weak immediate reclaim or absent next-bar "
    "MSS is no trade. The raid extreme must remain defended; within the existing "
    "delayed expiry, bullish displacement must close above the reclaim high and "
    "create a fresh FVG near the completed low. Entry is the completed confirmation "
    "close, invalidation is the local displacement leg low, and target is the "
    "opposite completed London boundary. No new numeric threshold or risk rule."
)
cp.write_text(json.dumps(c, indent=2) + "\n", encoding="utf-8")
