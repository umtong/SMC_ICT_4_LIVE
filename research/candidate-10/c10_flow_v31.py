"""Candidate 10 v3.1: enter the first retest of the absorbed boundary.

v3 established that executed aggressor flow removed all five losing price-only
trades, but its arbitrary 50% repricing-body entry left eight otherwise confirmed
flow reversals below the cost-adjusted structural R:R gate. v3.1 replaces only
the entry anchor: the passive order rests at the previously raided and reclaimed
local dealing-range boundary. The exact ablation restores the v3 midpoint entry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import c10_flow_strategy as _strategy_module
from c10_flow_model import FlowBar
from c10_flow_model import FlowRaidProbe
from c10_flow_model import FlowTradePlan
from c10_flow_precision_fix import run_flow_backtest as _run_flow_backtest
from c10_flow_state import FlowAuctionStateMachine as MidpointFlowAuctionStateMachine
from smc_ict_4.manifest import write_json_atomic


class BoundaryRetestFlowAuctionStateMachine(MidpointFlowAuctionStateMachine):
    """Anchor entry to the absorbed/reclaimed source boundary."""

    def _build_plan(
        self,
        bar: FlowBar,
        probe: FlowRaidProbe,
        features: dict[str, float],
    ) -> FlowTradePlan | None:
        atr = features["atr"]
        entry = probe.boundary
        if probe.direction < 0:
            stop = probe.raid_extreme + self._execution_buffer(entry, atr)
            target = probe.opposite_boundary
            valid = target < entry < stop and bar.close < entry
        else:
            stop = probe.raid_extreme - self._execution_buffer(entry, atr)
            target = probe.opposite_boundary
            valid = stop < entry < target and bar.close > entry
        if not valid:
            return None
        net_rr = self._net_rr(
            direction=probe.direction,
            entry=entry,
            stop=stop,
            target=target,
        )
        if net_rr < self.params.min_net_rr:
            return None
        return FlowTradePlan(
            scenario_id=probe.scenario_id,
            scenario="FLOW_ABSORPTION_REPRICING",
            direction=probe.direction,
            observed_ns=bar.end_ns,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            source_boundary=probe.boundary,
            opposite_boundary=probe.opposite_boundary,
            event_atr=atr,
            entry_expiry_bars=self.params.entry_expiry_bars,
            invalidation_price=stop,
            details={
                "cost_adjusted_net_rr": net_rr,
                "raid_delta_ratio": probe.initial_delta_ratio,
                "raid_efficiency": probe.initial_efficiency,
                "repricing_delta_ratio": bar.delta_ratio,
                "repricing_efficiency": bar.efficiency,
                "source_side": probe.source_side,
                "entry_anchor": "ABSORBED_SOURCE_BOUNDARY",
                "source_boundary": probe.boundary,
                "opposite_boundary": probe.opposite_boundary,
                "raid_extreme": probe.raid_extreme,
            },
        )


def run_v31_backtest(
    *,
    entry_at_source_boundary: bool,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the same Nautilus experiment with one controlled entry variable."""

    state_class = (
        BoundaryRetestFlowAuctionStateMachine
        if entry_at_source_boundary
        else MidpointFlowAuctionStateMachine
    )
    previous = _strategy_module.FlowAuctionStateMachine
    _strategy_module.FlowAuctionStateMachine = state_class
    try:
        metrics = _run_flow_backtest(**kwargs)
    finally:
        _strategy_module.FlowAuctionStateMachine = previous

    destination = Path(kwargs["output_dir"])
    metrics["candidate_generation"] = (
        "v3.1-absorbed-boundary-first-retest"
    )
    metrics["entry_anchor"] = (
        "ABSORBED_SOURCE_BOUNDARY"
        if entry_at_source_boundary
        else "REPRICING_BODY_MIDPOINT_ABLATION"
    )
    metrics["params"]["entry_at_source_boundary"] = entry_at_source_boundary
    write_json_atomic(destination / "metrics.json", metrics)

    run_path = destination / "run.json"
    if run_path.exists():
        run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
        run_manifest["candidate_generation"] = metrics["candidate_generation"]
        run_manifest["entry_anchor"] = metrics["entry_anchor"]
        write_json_atomic(run_path, run_manifest)
    return metrics


__all__ = [
    "BoundaryRetestFlowAuctionStateMachine",
    "MidpointFlowAuctionStateMachine",
    "run_v31_backtest",
]
