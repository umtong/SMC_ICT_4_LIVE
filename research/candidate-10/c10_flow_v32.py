"""Candidate 10 v3.2: micro flow trigger with a macro event-auction target.

v3 and v3.1 showed that signed executed flow removes adverse price-only entries,
but a 20-fast-event target is normally smaller than the cost-loaded stop budget.
This generation keeps the detector, order-flow certification, source-boundary
entry, stop, costs and risk unchanged. Only the destination changes: the
opposite edge of a slower event-notional auction built from one rolling median
minute of executed notional. The ablation restores the fast-auction target.
"""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from typing import Any

import c10_flow_strategy as _strategy_module
from c10_flow_model import FlowBar
from c10_flow_model import FlowTickView
from c10_flow_model import FlowTradePlan
from c10_flow_precision_fix import run_flow_backtest as _run_flow_backtest
from c10_flow_v31 import BoundaryRetestFlowAuctionStateMachine
from smc_ict_4.manifest import write_json_atomic


class MultiscaleFlowAuctionStateMachine(BoundaryRetestFlowAuctionStateMachine):
    """Use fast event bars for timing and a slower executed-notional auction for target."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.macro_current_bar: FlowBar | None = None
        self.macro_next_sequence = 0
        self.macro_completed_bars: deque[FlowBar] = deque(
            maxlen=self.params.range_event_bars + 32,
        )

    def diagnostics(self) -> dict[str, object]:
        result = dict(super().diagnostics())
        result.update(
            {
                "macro_completed_flow_bars": self.macro_next_sequence,
                "macro_range_history": len(self.macro_completed_bars),
                "macro_notional_definition": (
                    "one rolling median completed-minute aggregate-trade notional"
                ),
            },
        )
        return result

    def _update_macro_candidate(self, tick: FlowTickView) -> FlowBar | None:
        fine_threshold = self._event_threshold()
        if fine_threshold is None:
            return None
        if self.params.event_notional_fraction <= 0.0:
            raise ValueError("event_notional_fraction must be positive")
        macro_threshold = fine_threshold / self.params.event_notional_fraction
        if self.macro_current_bar is None:
            self.macro_current_bar = FlowBar.from_tick(
                sequence=self.macro_next_sequence,
                threshold_notional=macro_threshold,
                tick=tick,
            )
        else:
            self.macro_current_bar.update(tick)
        if self.macro_current_bar.notional < self.macro_current_bar.threshold_notional:
            return None
        completed = self.macro_current_bar
        self.macro_current_bar = None
        self.macro_next_sequence += 1
        return completed

    def on_tick(
        self,
        tick: FlowTickView,
    ) -> tuple[list[Any], FlowTradePlan | None, FlowBar | None]:
        if tick.quantity <= 0.0 or tick.price <= 0.0:
            raise ValueError("flow tick price and quantity must be positive")
        if tick.aggressor not in {-1, 1}:
            raise ValueError("flow tick aggressor must be +1 or -1")

        self._roll_minute(tick)
        macro_completed = self._update_macro_candidate(tick)

        if self.current_bar is None:
            threshold = self._event_threshold()
            if threshold is None:
                if macro_completed is not None:
                    self.macro_completed_bars.append(macro_completed)
                return [], None, None
            self.current_bar = FlowBar.from_tick(
                sequence=self.next_sequence,
                threshold_notional=threshold,
                tick=tick,
            )
        else:
            self.current_bar.update(tick)

        transitions: list[Any] = []
        plan: FlowTradePlan | None = None
        fine_completed: FlowBar | None = None
        if self.current_bar.notional >= self.current_bar.threshold_notional:
            fine_completed = self.current_bar
            self.current_bar = None
            self.next_sequence += 1
            # A macro bar which closes on the same aggregate trade is appended
            # only after the fine signal is evaluated. Target liquidity therefore
            # always existed strictly before the signal observation.
            transitions, plan = self._on_completed_bar(fine_completed)

        if macro_completed is not None:
            self.macro_completed_bars.append(macro_completed)
        return transitions, plan, fine_completed

    def _feature_snapshot(self) -> dict[str, float] | None:
        features = super()._feature_snapshot()
        if features is None:
            return None
        if len(self.macro_completed_bars) < self.params.range_event_bars:
            return None
        macro = list(self.macro_completed_bars)[-self.params.range_event_bars :]
        features["macro_range_high"] = max(item.high for item in macro)
        features["macro_range_low"] = min(item.low for item in macro)
        return features

    def _detect_absorption_raid(
        self,
        bar: FlowBar,
        features: dict[str, float],
    ) -> list[Any]:
        events = super()._detect_absorption_raid(bar, features)
        probe = self.active_probe
        if probe is None or not events:
            return events

        fine_target = probe.opposite_boundary
        macro_target = (
            features["macro_range_low"]
            if probe.direction < 0
            else features["macro_range_high"]
        )
        directionally_valid = (
            macro_target < probe.boundary
            if probe.direction < 0
            else macro_target > probe.boundary
        )
        if not directionally_valid:
            self.counters["MACRO_TARGET_NOT_DIRECTIONAL"] += 1
            self.active_probe = None
            return []

        probe.opposite_boundary = macro_target
        for event in events:
            if event.event_type == "ABSORPTION_PROBED":
                event.details["fine_opposite_boundary"] = fine_target
                event.details["opposite_boundary"] = macro_target
                event.details["target_scale"] = "MACRO_EVENT_NOTIONAL_AUCTION"
                event.details["macro_range_event_bars"] = self.params.range_event_bars
                event.details["macro_completed_sequence"] = self.macro_next_sequence - 1
        self.counters["MACRO_TARGET_ASSIGNED"] += 1
        return events


def run_v32_backtest(
    *,
    use_macro_target: bool,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run one controlled target-scale variant in the pinned Nautilus engine."""

    state_class = (
        MultiscaleFlowAuctionStateMachine
        if use_macro_target
        else BoundaryRetestFlowAuctionStateMachine
    )
    previous = _strategy_module.FlowAuctionStateMachine
    _strategy_module.FlowAuctionStateMachine = state_class
    try:
        metrics = _run_flow_backtest(**kwargs)
    finally:
        _strategy_module.FlowAuctionStateMachine = previous

    destination = Path(kwargs["output_dir"])
    metrics["candidate_generation"] = (
        "v3.2-micro-flow-trigger-macro-event-auction-target"
    )
    metrics["target_scale"] = (
        "MACRO_EVENT_NOTIONAL_AUCTION"
        if use_macro_target
        else "FAST_EVENT_RANGE_ABLATION"
    )
    metrics["params"]["use_macro_target"] = use_macro_target
    metrics["params"]["macro_event_notional_fraction"] = 1.0
    metrics["params"]["macro_range_event_bars"] = metrics["params"][
        "range_event_bars"
    ]
    write_json_atomic(destination / "metrics.json", metrics)

    run_path = destination / "run.json"
    if run_path.exists():
        run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
        run_manifest["candidate_generation"] = metrics["candidate_generation"]
        run_manifest["target_scale"] = metrics["target_scale"]
        write_json_atomic(run_path, run_manifest)
    return metrics


__all__ = [
    "BoundaryRetestFlowAuctionStateMachine",
    "MultiscaleFlowAuctionStateMachine",
    "run_v32_backtest",
]
