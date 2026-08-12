"""Execute the existing EasyChart policy at selected source-supported scales.

The source material lists 1h/15m/5m as day-trading timeframes and repeatedly
uses a higher structure with a lower-timeframe OB/FVG entry.  The baseline v4
runtime already generates both 1h->5m (``MACRO``) and 15m->1m (``MICRO``)
plans, but only MICRO plans were allowed to reach NautilusTrader.

This module does not create a new signal.  It reuses the frozen causal policy
to answer one narrow question: does the already-generated 1h->5m execution
layer add independent post-cost expectancy?

For the dual diagnostic, one confirmed 1h context event may submit at most one
trade across MACRO and MICRO.  This prevents a higher event and its nested
lower entry from being counted as two independent opportunities.
"""
from __future__ import annotations

from typing import Any, Iterable

import mtf_strategy as _base
from mtf_strategy_v4 import EasyChartMTFConfig


def parent_context_episode_key(plan: Any) -> str | None:
    """Return the live 1h event which owns a plan, when it is auditable."""

    scale_name = getattr(plan, "scale_name", None)
    setup_id = getattr(plan, "setup_id", None)
    if scale_name == "MACRO" and isinstance(setup_id, str):
        prefix = "MACRO:STRUCTURE:"
        if setup_id.startswith(prefix):
            return setup_id[len(prefix) :]

    side = getattr(getattr(plan, "side", None), "name", None)
    provenance: Iterable[str] = getattr(plan, "rule_provenance", ())
    marker = "ROUTER_OBSERVED:LIVE_1H_EVENT:"
    suffix = None if side is None else f":{side}"
    for item in provenance:
        if not isinstance(item, str) or not item.startswith(marker):
            continue
        value = item[len("ROUTER_OBSERVED:") :]
        if suffix is not None and value.endswith(suffix):
            value = value[: -len(suffix)]
        # LIVE_1H_EVENT:<path>:<kind>:<event-id>
        parts = value.split(":", 3)
        if len(parts) == 4 and parts[0] == "LIVE_1H_EVENT":
            return parts[3]
    return None


class ScaleExecutionStrategy(_base.EasyChartMTFStrategy):
    """Audited single-account shell with an explicit executable-scale policy."""

    EXECUTABLE_SCALES: frozenset[str] = frozenset({"MICRO"})
    ONE_TRADE_PER_PARENT_CONTEXT = False

    def __init__(self, config: EasyChartMTFConfig) -> None:
        super().__init__(config)
        self.claimed_parent_context_events: set[str] = set()

    def _scale_is_executable(self, plan: Any) -> bool:
        return getattr(plan, "scale_name", None) in self.EXECUTABLE_SCALES

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
                if not self._scale_is_executable(plan):
                    self._record(
                        "plan_retained_nonexecuting_scale_evidence",
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
                parent_key = parent_context_episode_key(plan)
                if (
                    self.ONE_TRADE_PER_PARENT_CONTEXT
                    and parent_key is not None
                    and parent_key in self.claimed_parent_context_events
                ):
                    self._record(
                        "plan_skipped_parent_context_already_traded",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        scale_name=plan.scale_name,
                        family=plan.family,
                        side=plan.side.name,
                        parent_context_event_id=parent_key,
                    )
                    continue
                self.plan_log[plan.plan_id] = plan
                plans.append((instrument_id, plan, parent_key))
                self._record("plan", **self._plan_event_values(plan))

        # A lower-timeframe execution wins only when candidates become
        # observable on the same close.  Otherwise the first causally available
        # plan is selected.  No retrospective quality score is used.
        ranked = sorted(
            plans,
            key=lambda item: (
                item[1].observed_time_ns,
                0 if item[1].scale_name == "MICRO" else 1,
                item[1].interaction_time_ns,
                item[1].setup_observed_time_ns,
                item[1].symbol,
                item[1].plan_id,
            ),
        )
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, (instrument_id, plan, parent_key) in enumerate(ranked, start=1):
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        parent_context_event_id=parent_key,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                    )
            else:
                selected_index = None
                for index, (instrument_id, plan, parent_key) in enumerate(ranked):
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        if self.ONE_TRADE_PER_PARENT_CONTEXT and parent_key is not None:
                            self.claimed_parent_context_events.add(parent_key)
                        self._record(
                            "arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                            parent_context_event_id=parent_key,
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = ranked[selected_index][1].plan_id
                    for rank, (instrument_id, plan, parent_key) in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "plan_skipped_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                            parent_context_event_id=parent_key,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None


class MacroOnlyEasyChartStrategy(ScaleExecutionStrategy):
    """Execute only the already-generated 1h->5m plans."""

    EXECUTABLE_SCALES = frozenset({"MACRO"})


class DualScaleFirstAvailableEasyChartStrategy(ScaleExecutionStrategy):
    """Execute first available MACRO/MICRO plan, once per 1h event."""

    EXECUTABLE_SCALES = frozenset({"MACRO", "MICRO"})
    ONE_TRADE_PER_PARENT_CONTEXT = True


__all__ = [
    "DualScaleFirstAvailableEasyChartStrategy",
    "EasyChartMTFConfig",
    "MacroOnlyEasyChartStrategy",
    "ScaleExecutionStrategy",
    "parent_context_episode_key",
]
