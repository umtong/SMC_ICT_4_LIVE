"""NautilusTrader binding for the EasyChart v5 structure-first policy.

The existing, already-audited v3 execution/account/portfolio implementation is
reused unchanged. This module replaces only the decision bundle and removes
one v3 arbitration preference which accidentally rewarded heterogeneous
OB/FVG labels. v5 routes by causal time and auction scale, not by indicator
count or kind diversity.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from nautilus_trader.model.identifiers import InstrumentId

import mtf_strategy as _base
from auction_context_v5 import AuctionContextSnapshot, AuctionState
from contracts_v5 import V5TradePlan
from cost_geometry_v5 import (
    NONPOSITIVE_TARGET_RULE,
    AfterCostTargetGeometry,
    conservative_after_cost_target,
)
from scenario_bundle_v5 import ResearchScenarioBundleV5


_base.MultiScaleScenarioBundle = ResearchScenarioBundleV5
EasyChartMTFConfig = _base.EasyChartMTFConfig


class EasyChartMTFStrategy(_base.EasyChartMTFStrategy):
    """One continuous four-symbol account with structure-first arbitration."""

    def _context_snapshots_for_plan(
        self,
        plan: V5TradePlan,
    ) -> dict[str, AuctionContextSnapshot]:
        output: dict[str, AuctionContextSnapshot] = {}
        for bundle in self.scenario_engines.values():
            snapshot = bundle.context_snapshot(plan.decision_timeframe_minutes)
            if snapshot is None:
                continue
            if snapshot.observed_time_ns > plan.observed_time_ns:
                raise RuntimeError("market context used information after plan observation")
            output[snapshot.symbol] = snapshot
        return output

    @staticmethod
    def _ordinal_rank(
        target_symbol: str,
        values: dict[str, float | None],
        *,
        descending: bool,
    ) -> int | None:
        finite = [
            (symbol, float(value))
            for symbol, value in values.items()
            if value is not None
        ]
        if target_symbol not in {symbol for symbol, _ in finite}:
            return None
        ordered = sorted(
            finite,
            key=lambda item: ((-item[1] if descending else item[1]), item[0]),
        )
        return next(
            rank
            for rank, (symbol, _) in enumerate(ordered, start=1)
            if symbol == target_symbol
        )

    @staticmethod
    def _aligned_state(side_value: int) -> AuctionState:
        return AuctionState.UP if side_value > 0 else AuctionState.DOWN

    @staticmethod
    def _opposite_state(side_value: int) -> AuctionState:
        return AuctionState.DOWN if side_value > 0 else AuctionState.UP

    def _record_plan_market_context(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> None:
        contexts = self._context_snapshots_for_plan(plan)
        target = contexts.get(plan.symbol)
        if target is None:
            raise RuntimeError(f"target context missing for {plan.plan_id}")
        btc = next(
            (snapshot for symbol, snapshot in contexts.items() if symbol.startswith("BTC")),
            None,
        )
        local_counts = Counter(snapshot.local_state.value for snapshot in contexts.values())
        structural_counts = Counter(
            snapshot.structural_state.value for snapshot in contexts.values()
        )
        aligned = self._aligned_state(int(plan.side.value))
        opposite = self._opposite_state(int(plan.side.value))
        returns = {symbol: snapshot.return_24h for symbol, snapshot in contexts.items()}
        side_signed_returns = {
            symbol: (
                None
                if value is None
                else int(plan.side.value) * float(value)
            )
            for symbol, value in returns.items()
        }
        activity = {
            symbol: snapshot.notional_volume_24h
            for symbol, snapshot in contexts.items()
        }
        all_contexts = {
            symbol: snapshot.to_dict()
            for symbol, snapshot in sorted(contexts.items())
        }
        self._record(
            "plan_market_context",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            symbol=plan.symbol,
            side=plan.side.name,
            scale_name=plan.scale_name,
            decision_timeframe_minutes=plan.decision_timeframe_minutes,
            plan_observed_time_ns=plan.observed_time_ns,
            target_context_time_ns=target.observed_time_ns,
            target_local_state=target.local_state.value,
            target_structural_state=target.structural_state.value,
            target_local_aligned=target.local_state is aligned,
            target_local_opposite=target.local_state is opposite,
            target_structural_aligned=target.structural_state is aligned,
            target_structural_opposite=target.structural_state is opposite,
            target_return_24h=target.return_24h,
            target_side_signed_return_rank=self._ordinal_rank(
                plan.symbol,
                side_signed_returns,
                descending=True,
            ),
            target_notional_volume_24h=target.notional_volume_24h,
            target_activity_rank=self._ordinal_rank(
                plan.symbol,
                activity,
                descending=True,
            ),
            target_range_position_24h=target.range_position_24h,
            btc_local_state=None if btc is None else btc.local_state.value,
            btc_structural_state=None if btc is None else btc.structural_state.value,
            btc_return_24h=None if btc is None else btc.return_24h,
            market_local_aligned_count=local_counts[aligned.value],
            market_local_opposite_count=local_counts[opposite.value],
            market_local_transition_count=local_counts[AuctionState.TRANSITION.value],
            market_local_unresolved_count=local_counts[AuctionState.UNRESOLVED.value],
            market_structural_aligned_count=structural_counts[aligned.value],
            market_structural_opposite_count=structural_counts[opposite.value],
            market_structural_transition_count=structural_counts[AuctionState.TRANSITION.value],
            market_structural_unresolved_count=structural_counts[AuctionState.UNRESOLVED.value],
            context_symbol_count=len(contexts),
            all_market_contexts=all_contexts,
        )

    def _after_cost_target_geometry(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> AfterCostTargetGeometry:
        instrument = self.instruments[instrument_id]
        return conservative_after_cost_target(
            is_long=plan.side.name == "LONG",
            entry=plan.entry,
            target=plan.target,
            price_increment=instrument.price_increment,
            entry_slippage_ticks=self.config.estimated_entry_slippage_ticks,
            entry_fee_rate=self.config.estimated_entry_fee_rate,
            # The native target is not post-only. Use the same conservative
            # exit fee already reserved for a stop rather than inventing a
            # favorable maker fill.
            target_fee_rate=self.config.estimated_stop_fee_rate,
            funding_rate=self.config.estimated_funding_rate,
        )

    def _submit_plan(self, instrument_id: InstrumentId, plan: V5TradePlan) -> bool:
        geometry = self._after_cost_target_geometry(instrument_id, plan)
        if not geometry.positive:
            self._record(
                "plan_rejected_nonpositive_net_target",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                side=plan.side.name,
                rule_provenance=NONPOSITIVE_TARGET_RULE,
                **geometry.to_float_dict(),
            )
            return False
        return super()._submit_plan(instrument_id, plan)

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        plans: list[tuple[InstrumentId, V5TradePlan]] = []
        for instrument_id, timeframe, bar in sorted(
            self.bar_bucket,
            key=lambda item: (-item[1], str(item[0])),
        ):
            engine = self.scenario_engines[instrument_id]
            emitted = engine.on_bar(timeframe, self._candle(bar))
            for transition in engine.drain_trace():
                if transition.get("event_time_ns", 0) >= self.config.trading_start_ns:
                    self._record(
                        "scenario_transition",
                        instrument_id=str(instrument_id),
                        timeframe_minutes=timeframe,
                        **transition,
                    )
            if bar.ts_event < self.config.trading_start_ns:
                continue
            for plan in emitted:
                self.plan_log[plan.plan_id] = plan
                plans.append((instrument_id, plan))
                self._record("plan", **self._plan_event_values(plan))

        # All symbols at this timestamp have now updated their corresponding
        # decision-timeframe contexts. Preserve the whole observable market
        # state before selecting one account-level opportunity. These fields
        # are diagnostics only here; they do not filter or rescale risk.
        for instrument_id, plan in plans:
            self._record_plan_market_context(instrument_id, plan)

        # The earliest completed causal episode wins. Larger auction scale is
        # the deterministic tie-breaker. No score, indicator count or risk
        # multiplier is introduced.
        ranked = sorted(
            plans,
            key=lambda item: (
                item[1].interaction_time_ns,
                -item[1].higher_timeframe_minutes,
                item[1].setup_observed_time_ns,
                item[1].symbol,
                item[1].plan_id,
            ),
        )
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                    )
            else:
                selected_index: int | None = None
                for index, (instrument_id, plan) in enumerate(ranked):
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = ranked[selected_index][1].plan_id
                    for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "plan_skipped_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None
