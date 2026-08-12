"""Bind the audited Nautilus strategy shell to the EasyChart v4 scene router."""
from __future__ import annotations

from typing import Any

import mtf_strategy as _base
from scenario_runtime_v4_preserved import ResearchScenarioBundleV4

_base.MultiScaleScenarioBundle = ResearchScenarioBundleV4
EasyChartMTFConfig = _base.EasyChartMTFConfig


def is_executable_easychart_plan(plan: Any) -> bool:
    """Only the lower-timeframe execution layer may submit an entry.

    EasyChart's source hierarchy assigns 12h/4h/1h to the medium scene
    (direction, pattern and support/resistance) and 15m/5m/1m to actual entry.
    The existing ``MACRO`` 1h->5m plan is still generated and audited as
    higher-scene evidence, but it is not a second parallel trading bot.
    """

    return getattr(plan, "scale_name", None) == "MICRO"


class EasyChartMTFStrategy(_base.EasyChartMTFStrategy):
    """One continuous account with deterministic, non-score arbitration."""

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        plans = []
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
                if not is_executable_easychart_plan(plan):
                    self._record(
                        "plan_retained_as_higher_context_evidence",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        scale_name=plan.scale_name,
                        family=plan.family,
                        side=plan.side.name,
                        interaction_time_ns=plan.interaction_time_ns,
                        observed_time_ns=plan.observed_time_ns,
                        higher_timeframe_minutes=plan.higher_timeframe_minutes,
                        decision_timeframe_minutes=plan.decision_timeframe_minutes,
                        trigger_timeframe_minutes=plan.trigger_timeframe_minutes,
                    )
                    continue
                self.plan_log[plan.plan_id] = plan
                plans.append((instrument_id, plan))
                self._record("plan", **self._plan_event_values(plan))

        # Simultaneous candidates are not scored. Earlier causal interaction
        # wins; a higher-timeframe interpretation of that same timestamp wins
        # the tie; remaining fields only make ordering reproducible.
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
                selected_index = None
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
