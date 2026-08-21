"""NautilusTrader binding for the EasyChart v5 structure-first policy.

The existing, already-audited v3 execution/account/portfolio implementation is
reused unchanged.  This module replaces only the decision bundle and removes
one v3 arbitration preference which accidentally rewarded heterogeneous
OB/FVG labels.  v5 routes by causal time and auction scale, not by indicator
count or kind diversity.
"""
from __future__ import annotations

from nautilus_trader.model.identifiers import InstrumentId

import mtf_strategy as _base
from contracts_v5 import V5TradePlan
from scenario_bundle_v5 import ResearchScenarioBundleV5


_base.MultiScaleScenarioBundle = ResearchScenarioBundleV5
EasyChartMTFConfig = _base.EasyChartMTFConfig


class EasyChartMTFStrategy(_base.EasyChartMTFStrategy):
    """One continuous four-symbol account with structure-first arbitration."""

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

        # The earliest completed causal episode wins.  Larger auction scale is
        # the deterministic tie-breaker.  No score, indicator count or risk
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
