"""Source-supported confirmation-close entry for learned Fakeout/Trap episodes.

EasyChart presents two confirmation approaches after a boundary is reclaimed:
enter after the reclaim candle closes, or wait for the standard retest. The
first BTC diagnostic showed many valid learned-boundary episodes but no retest
plans during the evaluation week; targets were often reached before a later
retest or the first retest did not print a directional reaction candle.

This class is an explicit ablation of entry timing, not a relaxed threshold. It
keeps the same learned boundary, causal episode, stop, target, costs, account
and global router. Only the source-supported decision time changes:

* Fakeout: plan at the owner reclaim close;
* Trap: plan when both W/M topology and owner reentry are observable;
* accepted breaks remain terminal and are never reversed.

The market parent is submitted after the complete timestamp bucket, so it can
only fill on later market data.
"""
from __future__ import annotations

from contracts_v5 import V5TradePlan
from domain import Candle
from learned_horizontal_v7 import (
    LearnedHorizontalScenarioEngine,
    LearnedHorizontalSetup,
    LearnedHorizontalZone,
    LearnedSetupState,
)
import learned_horizontal_v7_runtime  # noqa: F401 - lifecycle invariants


class ConfirmationCloseLearnedHorizontalScenarioEngine(LearnedHorizontalScenarioEngine):
    ENTRY_POLICY = "CONFIRMATION_CLOSE"

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._confirmation_output: list[V5TradePlan] = []

    def _plan_confirmation(
        self,
        setup: LearnedHorizontalSetup,
        bar: Candle,
    ) -> V5TradePlan | None:
        if setup.target_zone is None or setup.target_price is None:
            self._finish(
                setup,
                LearnedSetupState.NO_TARGET,
                bar.ts_close_ns,
                "learned_confirmation_lost_target",
            )
            return None
        entry = bar.close
        stop = self._stop_price(setup)
        target = setup.target_price
        valid = stop < entry < target if setup.side.value > 0 else target < entry < stop
        if not valid:
            self._finish(
                setup,
                LearnedSetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "learned_confirmation_invalid_geometry",
                entry=entry,
                stop=stop,
                target=target,
            )
            return None
        gross_rr = abs(target - entry) / abs(entry - stop)
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                LearnedSetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "learned_confirmation_gross_rr_below_minimum",
                gross_rr=gross_rr,
            )
            return None

        self.sequence += 1
        family = f"{self.scale_name}_LEARNED_HORIZONTAL_{setup.path}_CONFIRMATION_CLOSE"
        plan = V5TradePlan(
            plan_id=f"ecv7-lhc-{self.scale_name.lower()}-{self.symbol}-{self.sequence:08d}",
            causal_event_id=f"{family}:{setup.setup_id}",
            symbol=self.symbol,
            family=family,
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=setup.zone.zone_id,
            higher_zone_kind=setup.zone.kind,
            higher_strength_ratio=setup.zone.strength_ratio,
            lower_zone_id=setup.zone.zone_id,
            lower_zone_kind=setup.zone.kind,
            lower_strength_ratio=setup.zone.strength_ratio,
            trigger_zone_id=setup.zone.zone_id,
            trigger_strength_ratio=setup.zone.strength_ratio,
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            overlap_lower=setup.zone.lower,
            overlap_upper=setup.zone.upper,
            interaction_time_ns=setup.interaction_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=setup.path,
            setup_observed_time_ns=setup.zone.observed_time_ns,
            trigger_zone_kind="LEARNED_HORIZONTAL_CONFIRMATION_CLOSE",
            source_rule_count=len(self.SOURCE_RULES),
            rule_provenance=self.SOURCE_RULES
            + self.TRANSLATION_RULES
            + (
                "SOURCE_EXPLICIT:RECLAIM_CLOSE_ENTRY_IS_AN_ALLOWED_CONFIRMATION_METHOD",
                "RESEARCH_ABLATION:CONFIRMATION_CLOSE_REPLACES_LATER_RETEST_ENTRY",
            ),
            scale_name=self.scale_name,
            higher_timeframe_minutes=self.context_minutes,
            decision_timeframe_minutes=self.context_minutes,
            trigger_timeframe_minutes=self.trigger_minutes,
        )
        setup.state = LearnedSetupState.PLANNED
        self._active.pop(setup.setup_id, None)
        self.plans.append(plan)
        self._inc("learned_confirmation_plan_created")
        self._trace(
            "learned_confirmation_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            family=family,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
        )
        return plan

    def _emit_confirmation(
        self,
        setup: LearnedHorizontalSetup,
        bar: Candle,
    ) -> None:
        if setup.state is not LearnedSetupState.WAITING_RETEST:
            return
        if setup.confirmation_time_ns is None or setup.confirmation_time_ns != bar.ts_close_ns:
            return
        if self._target_spent(setup, bar):
            self._finish(
                setup,
                LearnedSetupState.TARGET_SPENT,
                bar.ts_close_ns,
                "learned_target_spent_at_confirmation",
            )
            return
        plan = self._plan_confirmation(setup, bar)
        if plan is not None:
            self._confirmation_output.append(plan)

    def _new_setup(
        self,
        zone: LearnedHorizontalZone,
        bar: Candle,
        index: int,
        *,
        fakeout: bool,
    ) -> LearnedHorizontalSetup:
        setup = super()._new_setup(zone, bar, index, fakeout=fakeout)
        if fakeout:
            self._emit_confirmation(setup, bar)
        return setup

    def _context_bar(self, bar: Candle) -> list[V5TradePlan]:
        self._confirmation_output = []
        self._advance_context_setups(bar)
        for setup in list(self._active.values()):
            self._emit_confirmation(setup, bar)
        self._discover_context_interactions(bar, len(self.detector.bars))
        for zone in self.detector.on_bar(bar):
            self._audit(zone)
        return self._confirmation_output

    def _trigger_bar(self, bar: Candle) -> list[V5TradePlan]:
        if self.trigger_bars and bar.ts_close_ns <= self.trigger_bars[-1].ts_close_ns:
            raise ValueError("trigger bars must arrive in increasing close time")
        self._confirmation_output = []
        self.trigger_bars.append(bar)
        for pivot in self._confirmed_trigger_pivots():
            self._update_trap_topology(pivot)
        for setup in list(self._active.values()):
            self._emit_confirmation(setup, bar)
        # Reentry without a confirmed W/M still consumes a first line retest;
        # this prevents a later prettier topology from reviving a stale entry.
        remaining = self._advance_trigger_setups(bar)
        return self._confirmation_output + remaining
