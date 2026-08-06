"""Candidate 10 v4: efficient acceptance continuation inside a macro auction.

This is a separate trading scenario, not a filter added to the absorption
reversal. A fast local dealing-range boundary is accepted only when price closes
outside efficiently with same-side executed aggressor flow. Entry rests on the
first retest of the broken fast boundary, invalidation is back inside the old
range by the executable buffer, and the target is the already-existing same-side
edge of the slower event-notional auction. The exact ablation removes only the
same-side aggressor-flow requirement at acceptance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import c10_flow_strategy as _strategy_module
from c10_flow_model import FlowBar
from c10_flow_model import FlowTradePlan
from c10_flow_precision_fix import run_flow_backtest as _run_flow_backtest
from c10_flow_v32 import MultiscaleFlowAuctionStateMachine
from smc_ict_4.manifest import write_json_atomic


class AcceptanceContinuationFlowAuctionStateMachine(
    MultiscaleFlowAuctionStateMachine,
):
    """Trade efficient local acceptance toward pre-existing macro liquidity."""

    require_acceptance_order_flow = True

    def _on_completed_bar(
        self,
        bar: FlowBar,
    ) -> tuple[list[Any], FlowTradePlan | None]:
        features = self._feature_snapshot()
        transitions: list[Any] = []
        plan: FlowTradePlan | None = None
        if features is not None:
            transitions, plan = self._detect_acceptance_continuation(bar, features)

        true_range = bar.true_range(self.previous_flow_close)
        self.previous_flow_close = bar.close
        self.true_ranges.append(true_range)
        self.abs_delta_history.append(abs(bar.delta_ratio))
        self.efficiency_history.append(bar.efficiency)
        self.completed_bars.append(bar)
        self.counters["FLOW_BAR_COMPLETED"] += 1
        return transitions, plan

    def _detect_acceptance_continuation(
        self,
        bar: FlowBar,
        features: dict[str, float],
    ) -> tuple[list[Any], FlowTradePlan | None]:
        atr = features["atr"]
        range_high = features["range_high"]
        range_low = features["range_low"]
        extension = self.params.raid_atr * atr

        high_acceptance = bar.close >= range_high + extension
        low_acceptance = bar.close <= range_low - extension
        if high_acceptance and low_acceptance:
            self.counters["AMBIGUOUS_ACCEPTANCE"] += 1
            return [], None
        if not high_acceptance and not low_acceptance:
            return [], None
        self.counters["PRICE_ACCEPTANCE_OBSERVED"] += 1

        efficient = bar.efficiency >= features["repricing_efficiency"]
        if high_acceptance:
            direction = 1
            entry = range_high
            target = features["macro_range_high"]
            directional_price = (
                bar.net_move > 0.0
                and bar.close_location >= 0.65
                and target > bar.close > entry
            )
            flow_confirmed = bar.delta_ratio >= features["delta_extreme"]
            stop = entry - self._execution_buffer(entry, atr)
            valid_geometry = stop < entry < target
            source_side = "HIGH"
        else:
            direction = -1
            entry = range_low
            target = features["macro_range_low"]
            directional_price = (
                bar.net_move < 0.0
                and bar.close_location <= 0.35
                and target < bar.close < entry
            )
            flow_confirmed = bar.delta_ratio <= -features["delta_extreme"]
            stop = entry + self._execution_buffer(entry, atr)
            valid_geometry = target < entry < stop
            source_side = "LOW"

        if not efficient or not directional_price:
            self.counters["ACCEPTANCE_PRICE_RESPONSE_REJECTED"] += 1
            return [], None
        if self.require_acceptance_order_flow and not flow_confirmed:
            self.counters["ACCEPTANCE_FLOW_REJECTED"] += 1
            return [], None
        if not valid_geometry:
            self.counters["NO_PREEXISTING_MACRO_TARGET"] += 1
            return [], None

        net_rr = self._net_rr(
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
        )
        if net_rr < self.params.min_net_rr:
            self.counters["ACCEPTANCE_COST_RR_REJECTED"] += 1
            return [], None

        self.scenario_sequence += 1
        scenario_id = (
            f"{self.instrument_id}:ACCEPT:{self.scenario_sequence:06d}"
        )
        plan = FlowTradePlan(
            scenario_id=scenario_id,
            scenario="FLOW_ACCEPTANCE_CONTINUATION",
            direction=direction,
            observed_ns=bar.end_ns,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            source_boundary=entry,
            opposite_boundary=target,
            event_atr=atr,
            entry_expiry_bars=self.params.entry_expiry_bars,
            invalidation_price=stop,
            details={
                "cost_adjusted_net_rr": net_rr,
                "acceptance_delta_ratio": bar.delta_ratio,
                "acceptance_delta_threshold": features["delta_extreme"],
                "acceptance_efficiency": bar.efficiency,
                "acceptance_efficiency_threshold": features[
                    "repricing_efficiency"
                ],
                "source_side": source_side,
                "entry_anchor": "ACCEPTED_FAST_BOUNDARY",
                "target_scale": "SAME_SIDE_MACRO_EVENT_AUCTION_EDGE",
                "fast_range_high": range_high,
                "fast_range_low": range_low,
                "macro_range_high": features["macro_range_high"],
                "macro_range_low": features["macro_range_low"],
                "acceptance_order_flow_required": (
                    self.require_acceptance_order_flow
                ),
            },
        )
        self.counters["ACCEPTANCE_TRADE_PLAN_CREATED"] += 1
        transition = self._transition(
            scenario_id=scenario_id,
            bar=bar,
            event_type="ACCEPTANCE_CONFIRMED",
            previous_state="FAST_RANGE_ACTIVE",
            next_state="ENTRY_READY",
            reason_code=(
                "SAME_SIDE_AGGRESSOR_FLOW_EFFICIENTLY_ACCEPTED_BOUNDARY"
                if self.require_acceptance_order_flow
                else "PRICE_EFFICIENCY_ACCEPTANCE_FLOW_ABLATION"
            ),
            reference_price=entry,
            details={
                "direction": direction,
                "source_side": source_side,
                "entry": entry,
                "stop": stop,
                "target": target,
                "cost_adjusted_net_rr": net_rr,
                "delta_ratio": bar.delta_ratio,
                "delta_threshold": features["delta_extreme"],
                "price_efficiency": bar.efficiency,
                "efficiency_threshold": features["repricing_efficiency"],
                "close_location": bar.close_location,
                "acceptance_order_flow_required": (
                    self.require_acceptance_order_flow
                ),
            },
        )
        return [transition], plan


class PriceOnlyAcceptanceContinuationStateMachine(
    AcceptanceContinuationFlowAuctionStateMachine,
):
    require_acceptance_order_flow = False


def run_v4_backtest(
    *,
    require_acceptance_order_flow: bool,
    **kwargs: Any,
) -> dict[str, Any]:
    state_class = (
        AcceptanceContinuationFlowAuctionStateMachine
        if require_acceptance_order_flow
        else PriceOnlyAcceptanceContinuationStateMachine
    )
    previous = _strategy_module.FlowAuctionStateMachine
    _strategy_module.FlowAuctionStateMachine = state_class
    try:
        metrics = _run_flow_backtest(**kwargs)
    finally:
        _strategy_module.FlowAuctionStateMachine = previous

    destination = Path(kwargs["output_dir"])
    metrics["candidate_generation"] = (
        "v4-efficient-flow-acceptance-continuation"
    )
    metrics["scenario_family"] = "FLOW_ACCEPTANCE_CONTINUATION"
    metrics["params"]["require_acceptance_order_flow"] = (
        require_acceptance_order_flow
    )
    metrics["params"]["macro_event_notional_fraction"] = 1.0
    metrics["params"]["macro_range_event_bars"] = metrics["params"][
        "range_event_bars"
    ]
    write_json_atomic(destination / "metrics.json", metrics)

    run_path = destination / "run.json"
    if run_path.exists():
        run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
        run_manifest["candidate_generation"] = metrics["candidate_generation"]
        run_manifest["scenario_family"] = metrics["scenario_family"]
        write_json_atomic(run_path, run_manifest)
    return metrics


__all__ = [
    "AcceptanceContinuationFlowAuctionStateMachine",
    "PriceOnlyAcceptanceContinuationStateMachine",
    "run_v4_backtest",
]
