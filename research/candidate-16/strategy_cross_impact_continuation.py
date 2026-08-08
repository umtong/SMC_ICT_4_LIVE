"""Lagged cross-asset OFI continuation on a newly starting local auction leg.

Roles are deliberately separated:

* prior completed peer OFI shock: directional context;
* target underreaction to peer repricing: latent market state;
* current target tail-flow/depth response: state transition and entry;
* current local structure: invalidation;
* pre-existing liquidity pools: objective/target.

The strategy reuses Candidate-05's Nautilus bracket, cost-aware current-NAV
3% risk sizing, liquidity-target selection, funding exits, and account logic.
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.data import Bar

from cross_impact_context import CrossImpactObservation
from cross_impact_context import LAGGED_CROSS_IMPACT_CONTEXT
from strategy_base import LiquidityResponseConfig
from strategy_base import LiquidityResponseStrategy
from strategy_base import PendingSetup


SCENARIO_FAMILY = "LAGGED_CROSS_ASSET_OFI_CONTINUATION"


def symbol_from_instrument(value: Any) -> str:
    text = str(value)
    if "-PERP" not in text:
        raise ValueError(f"unexpected project instrument id: {text}")
    return text.split("-PERP", 1)[0]


def _ready(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


class LaggedCrossImpactContinuationStrategy(LiquidityResponseStrategy):
    """Trade delayed local transmission of a completed multi-peer OFI shock."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        self.cross_impact_symbol = symbol_from_instrument(config.instrument_id)
        self._cross_impact_last_peer_event_ns = 0
        super().__init__(config)
        self.diagnostics.update(
            {
                "cross_impact_states_published": 0,
                "cross_impact_evaluations": 0,
                "cross_impact_actionable": 0,
                "cross_impact_entry_submissions": 0,
                "cross_impact_duplicate_peer_events": 0,
                "cross_impact_reasons": {},
            },
        )

    def on_start(self) -> None:
        LAGGED_CROSS_IMPACT_CONTEXT.ensure_run(
            (int(self.config.evaluation_start_ns), int(self.config.evaluation_end_ns)),
        )
        super().on_start()

    def on_bar(self, bar: Bar) -> None:
        # The inherited state machine calls our overridden detector before this
        # bar is published.  Every cross-symbol read therefore remains strict
        # prior-completed information even when Nautilus dispatches equal-time
        # bars to strategy instances in an arbitrary order.
        super().on_bar(bar)
        observation = self._current_cross_impact_observation()
        if observation is None:
            return
        LAGGED_CROSS_IMPACT_CONTEXT.publish(observation)
        self.diagnostics["cross_impact_states_published"] = int(
            self.diagnostics["cross_impact_states_published"],
        ) + 1

    def _current_cross_impact_observation(self) -> CrossImpactObservation | None:
        if len(self.bars) < 2:
            return None
        feature = self.current_feature
        if feature is None or not _ready(feature.get("feature_ready", False)):
            return None
        row = self.bars[-1]
        ts_event = int(row["ts"])
        observed = int(feature.get("observed_time_ns", 0) or 0)
        age_seconds = (ts_event - observed) / 1_000_000_000
        if age_seconds < -1e-9:
            raise RuntimeError("future feature reached cross-impact publisher")
        if age_seconds > self.config.feature_max_age_seconds:
            return None
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return None
        previous_close = float(self.bars[-2]["close"])
        return CrossImpactObservation(
            symbol=self.cross_impact_symbol,
            ts_event=ts_event,
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            flow_3m=self._feature("flow_3m"),
            ret_atr=(float(row["close"]) - previous_close) / atr,
            efficiency_60s=self._feature("efficiency_60s"),
            notional_burst=self._feature("notional_burst"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
        )

    def _count_reason(self, reason: str) -> None:
        reasons = self.diagnostics["cross_impact_reasons"]
        reasons[reason] = int(reasons.get(reason, 0)) + 1

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        del previous_close
        current = self._current_cross_impact_observation()
        if current is None:
            self._count_reason("CURRENT_OBSERVATION_UNAVAILABLE")
            return

        self.diagnostics["cross_impact_evaluations"] = int(
            self.diagnostics["cross_impact_evaluations"],
        ) + 1
        decision = LAGGED_CROSS_IMPACT_CONTEXT.decide(
            target_symbol=self.cross_impact_symbol,
            current=current,
        )
        self._count_reason(decision.reason)
        if not decision.actionable:
            return
        if decision.peer_event_time_ns <= self._cross_impact_last_peer_event_ns:
            self.diagnostics["cross_impact_duplicate_peer_events"] = int(
                self.diagnostics["cross_impact_duplicate_peer_events"],
            ) + 1
            return

        self._cross_impact_last_peer_event_ns = decision.peer_event_time_ns
        self.diagnostics["cross_impact_actionable"] = int(
            self.diagnostics["cross_impact_actionable"],
        ) + 1
        side = int(decision.side)
        atr = self._atr()
        recent = list(self.bars)[-3:]
        structure = (
            min(float(item["low"]) for item in recent)
            if side > 0
            else max(float(item["high"]) for item in recent)
        )

        self.scenario_counter += 1
        scenario_id = f"cross-impact-{self.scenario_counter:07d}"
        details = {
            "scenario_family": SCENARIO_FAMILY,
            "target_symbol": self.cross_impact_symbol,
            "decision": decision.to_dict(),
            "entry_transition": {
                "flow_15s": current.flow_15s,
                "flow_60s": current.flow_60s,
                "flow_3m": current.flow_3m,
                "ret_atr": current.ret_atr,
                "efficiency_60s": current.efficiency_60s,
                "notional_burst": current.notional_burst,
                "depth_imbalance_1": current.depth_imbalance_1,
            },
            "invalidation_structure": structure,
            "invalidation_source": "CURRENT_THREE_BAR_LOCAL_EXTREME",
            "objective_source": "PREEXISTING_DIRECTIONAL_LIQUIDITY_OR_COST_VALID_FALLBACK",
        }
        setup = PendingSetup(
            scenario_id=scenario_id,
            branch=SCENARIO_FAMILY,
            side=side,
            swept_kind="LOW" if side > 0 else "HIGH",
            pool_id=f"cross-impact-context-{decision.peer_event_time_ns}",
            pool_level=structure,
            created_index=self.bar_index,
            expires_index=self.bar_index + 1,
            sweep_extreme=structure,
            structure=structure,
            atr=atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        self.pending = setup
        self._transition(
            scenario_id,
            "LAGGED_CROSS_IMPACT_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_ARMED",
            decision.reason,
            float(row["close"]),
            details,
        )
        submitted = bool(self._submit_entry(setup, row))
        if submitted:
            self.diagnostics["cross_impact_entry_submissions"] = int(
                self.diagnostics["cross_impact_entry_submissions"],
            ) + 1
        elif self.pending is setup:
            self._expire_pending(row, "CROSS_IMPACT_ENTRY_NOT_SUBMITTED")


__all__ = [
    "LaggedCrossImpactContinuationStrategy",
    "SCENARIO_FAMILY",
    "symbol_from_instrument",
]
