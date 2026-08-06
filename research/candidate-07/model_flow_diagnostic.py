"""Lossless diagnostics for the consumed-pool aggressor-flow router.

This subclass preserves the candidate's exact signal and trade-plan logic. It
only records which structural geometry condition rejected a confirmed episode,
so implementation and scenario failures can be separated before changing any
threshold.
"""
from __future__ import annotations

from typing import Any, Mapping

from model import Direction, ScenarioKind, ScenarioState, TradePlan, Transition
from model_flow import CausalAggressorFlowRouter, FlowSignalBar


class DiagnosticAggressorFlowRouter(CausalAggressorFlowRouter):
    def __init__(self, config):
        super().__init__(config)
        self._geometry_diagnostic: dict[str, Any] = {}

    def _build_plan(
        self,
        episode,
        bar: FlowSignalBar,
        atr: float,
        age: int,
    ) -> TradePlan | None:
        entry = bar.close
        buffer = self.config.stop_buffer_atr * atr
        if episode.direction is Direction.LONG:
            raw_stop = min(episode.extreme, episode.liquidity_level) - buffer
            minimum_stop = entry - self.config.minimum_stop_atr * atr
            stop = min(raw_stop, minimum_stop)
            risk = entry - stop
        else:
            raw_stop = max(episode.extreme, episode.liquidity_level) + buffer
            minimum_stop = entry + self.config.minimum_stop_atr * atr
            stop = max(raw_stop, minimum_stop)
            risk = stop - entry
        risk_atr = risk / atr if atr > 0.0 else 0.0
        common = {
            "entry": entry,
            "stop": stop,
            "risk": risk,
            "risk_atr": risk_atr,
            "atr": atr,
            "sweep_extreme": episode.extreme,
            "liquidity_level": episode.liquidity_level,
            "opposing_internal": episode.opposing_internal,
            "opposing_external": episode.opposing_external,
            "minimum_stop_atr": self.config.minimum_stop_atr,
            "maximum_stop_atr": self.config.maximum_stop_atr,
            "minimum_rr": self.config.minimum_rr,
        }
        if risk <= 0.0:
            self._geometry_diagnostic = {**common, "geometry_reason": "NONPOSITIVE_RISK"}
            return None
        if risk_atr < self.config.minimum_stop_atr:
            self._geometry_diagnostic = {**common, "geometry_reason": "STOP_TOO_TIGHT"}
            return None
        if risk_atr > self.config.maximum_stop_atr:
            self._geometry_diagnostic = {**common, "geometry_reason": "STOP_TOO_WIDE"}
            return None

        candidates: list[float] = []
        for level in (episode.opposing_internal, episode.opposing_external):
            if episode.direction is Direction.LONG and level > entry:
                candidates.append(level)
            elif episode.direction is Direction.SHORT and level < entry:
                candidates.append(level)
        if not candidates:
            self._geometry_diagnostic = {
                **common,
                "geometry_reason": "NO_OPPOSING_LIQUIDITY",
            }
            return None
        target_level = min(candidates) if episode.direction is Direction.LONG else max(candidates)
        target_rr = abs(target_level - entry) / risk
        target_common = {
            **common,
            "target_level": target_level,
            "uncapped_target_rr": target_rr,
        }
        if target_rr < self.config.minimum_rr:
            self._geometry_diagnostic = {
                **target_common,
                "geometry_reason": "TARGET_RR_BELOW_MINIMUM",
            }
            return None

        self._geometry_diagnostic = {**target_common, "geometry_reason": "ACCEPTED"}
        target_rr = min(target_rr, self.config.maximum_target_rr)
        target = (
            entry + risk * target_rr
            if episode.direction is Direction.LONG
            else entry - risk * target_rr
        )
        return TradePlan(
            scenario_id=episode.scenario_id,
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=episode.direction,
            observed_time_ns=bar.ts_event_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=episode.liquidity_level,
            expected_rr=target_rr,
            details={
                "atr": atr,
                "route_age_bars": age,
                "opposing_internal": episode.opposing_internal,
                "opposing_external": episode.opposing_external,
                "pool_formed_ns": episode.liquidity_formed_ns,
                "confirmation_imbalance": bar.imbalance,
            },
        )

    def _transition(
        self,
        episode,
        next_state: ScenarioState,
        reason: str,
        bar: FlowSignalBar,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> Transition:
        enriched = dict(details)
        if reason == "UNTRADEABLE_FLOW_GEOMETRY":
            enriched.update(self._geometry_diagnostic)
        return super()._transition(
            episode,
            next_state,
            reason,
            bar,
            reference_price,
            enriched,
        )


__all__ = ["DiagnosticAggressorFlowRouter"]
