"""Liquidity-acquisition-qualified 15-minute order blocks for EasyChart RE1.

The supplied material and live commentary do not call every engulfing candle an
important order block.  The repeatedly emphasized case is an order block formed
*while prior liquidity is taken*.  This module translates that complete auction
without adding a fitted score or another global filter:

1. a confirmed, still-available 15-minute wick swing must pre-exist before the
   source candle opens;
2. the source/impulse pair must trade beyond that swing and the impulse close
   must reclaim it in the order-block direction;
3. the displacement must retain the existing aligned one-minute taker-flow and
   price-progress validation;
4. the first later return keeps the inherited visual-response OR current
   absorption entry, natural structural stop and first pre-existing objective.

Thus structure identifies the liquidity pool, the source/impulse pair proves it
was raided and reclaimed, and taker flow proves the resulting expansion was a
real traded initiative.  No ATR, session, percentile, clock timeout, partial
management or outcome-dependent rule is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from easychart_re1_flow_ob import (
    FLOW_OB_FIRST_TOUCH_RULE,
    FLOW_VALIDATED_OB_FORMATION_RULE,
    EasyChartRE1PhaseFlowOBBundle,
    FlowValidatedDecisionAreaEngine,
    FlowValidatedOrderBlockDecisionStructureBook,
)
from easychart_zones import PriceZone, ZoneKind, ZoneSide


LIQUIDITY_TAKING_OB_RULE = (
    "SOURCE_EXPLICIT:"
    "IMPORTANT_ORDER_BLOCK_FORMS_WHILE_TAKING_PREEXISTING_LIQUIDITY"
)
CAUSAL_SWING_LIQUIDITY_PROXY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "A_CONFIRMED_UNSPENT_FIFTEEN_MINUTE_WICK_SWING_IS_THE_MACHINE_PROXY_FOR_PREEXISTING_LIQUIDITY_AT_OB_FORMATION"
)
if LIQUIDITY_TAKING_OB_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (LIQUIDITY_TAKING_OB_RULE,)
if CAUSAL_SWING_LIQUIDITY_PROXY_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (CAUSAL_SWING_LIQUIDITY_PROXY_RULE,)


@dataclass(frozen=True, slots=True)
class OBSweepEvidence:
    source_zone_id: str
    pivot_id: str
    pivot_side: str
    pivot_price: float
    pivot_span: int
    pivot_observed_time_ns: int
    source_open_time_ns: int
    source_close_time_ns: int
    impulse_close_time_ns: int
    sweep_time_ns: int
    sweep_extreme: float
    reclaim_close: float
    penetration: float


class LiquiditySweepFlowValidatedOBBook(FlowValidatedOrderBlockDecisionStructureBook):
    """Register only flow-valid OBs whose birth raids known swing liquidity."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sweep_evidence: dict[str, OBSweepEvidence] = {}
        self._sweep_validation_counts: dict[str, int] = {}

    def _sinc(self, key: str) -> None:
        self._sweep_validation_counts[key] = self._sweep_validation_counts.get(key, 0) + 1

    def _sweep_evidence(self, zone: PriceZone) -> OBSweepEvidence | None:
        formation = tuple(zone.formation_indices)
        if len(formation) < 2:
            self._sinc("formation_missing_source_impulse_pair")
            return None
        source_index, impulse_index = formation[0], formation[-1]
        if not (0 <= source_index < len(self.bars) and 0 <= impulse_index < len(self.bars)):
            raise RuntimeError("order-block formation indices exceed structure history")

        source = self.bars[source_index]
        impulse = self.bars[impulse_index]
        source_open_time_ns = source.ts_close_ns - (
            self.timeframe_minutes * 60 * 1_000_000_000
        )
        wanted = "LOW" if zone.side is ZoneSide.SUPPORT else "HIGH"
        candidates = [
            pivot
            for pivot in self.pivots
            if pivot.side == wanted
            and pivot.observed_time_ns <= source_open_time_ns
            and not (
                pivot.consumed_time_ns is not None
                and pivot.consumed_time_ns < source_open_time_ns
            )
        ]
        if not candidates:
            self._sinc("formation_no_preexisting_available_swing_liquidity")
            return None

        pair = self.bars[source_index : impulse_index + 1]
        if zone.side is ZoneSide.SUPPORT:
            extreme_bar = min(pair, key=lambda bar: (bar.low, bar.ts_close_ns))
            sweep_extreme = extreme_bar.low
            reclaimed = [
                pivot
                for pivot in candidates
                if sweep_extreme < pivot.price and impulse.close > pivot.price
            ]
            selected = max(
                reclaimed,
                key=lambda pivot: (
                    pivot.price,
                    pivot.span,
                    pivot.strength_ratio,
                    pivot.pivot_id,
                ),
                default=None,
            )
            penetration = 0.0 if selected is None else selected.price - sweep_extreme
        else:
            extreme_bar = max(pair, key=lambda bar: (bar.high, -bar.ts_close_ns))
            sweep_extreme = extreme_bar.high
            reclaimed = [
                pivot
                for pivot in candidates
                if sweep_extreme > pivot.price and impulse.close < pivot.price
            ]
            selected = min(
                reclaimed,
                key=lambda pivot: (
                    pivot.price,
                    -pivot.span,
                    -pivot.strength_ratio,
                    pivot.pivot_id,
                ),
                default=None,
            )
            penetration = 0.0 if selected is None else sweep_extreme - selected.price

        if selected is None:
            self._sinc("formation_pair_did_not_sweep_and_reclaim_preexisting_swing")
            return None
        self._sinc("formation_preexisting_swing_swept_and_reclaimed")
        return OBSweepEvidence(
            source_zone_id=zone.zone_id,
            pivot_id=selected.pivot_id,
            pivot_side=selected.side,
            pivot_price=selected.price,
            pivot_span=selected.span,
            pivot_observed_time_ns=selected.observed_time_ns,
            source_open_time_ns=source_open_time_ns,
            source_close_time_ns=source.ts_close_ns,
            impulse_close_time_ns=impulse.ts_close_ns,
            sweep_time_ns=extreme_bar.ts_close_ns,
            sweep_extreme=sweep_extreme,
            reclaim_close=impulse.close,
            penetration=penetration,
        )

    def _register(self, zone: PriceZone) -> None:
        if (
            zone.kind is not ZoneKind.ORDER_BLOCK
            or not zone.high_quality_by_size
            or zone.zone_id in self._source_ids
        ):
            return
        evidence = self._sweep_evidence(zone)
        if evidence is None:
            self._source_ids.add(zone.zone_id)
            self._sinc("formation_ob_rejected_without_liquidity_take")
            return

        super()._register(zone)
        level_id = f"DECISION_OB:{zone.zone_id}"
        if level_id not in self.flow_evidence:
            self._sinc("formation_sweep_ob_rejected_by_displacement_flow")
            return
        self.sweep_evidence[level_id] = evidence
        self._sinc("formation_liquidity_sweep_flow_ob_validated")

    @property
    def flow_validation_diagnostics(self) -> dict[str, Any]:
        output = dict(super().flow_validation_diagnostics)
        output["liquidity_sweep"] = {
            "counts": dict(sorted(self._sweep_validation_counts.items())),
            "validated_levels": len(self.sweep_evidence),
            "rules": (
                LIQUIDITY_TAKING_OB_RULE,
                CAUSAL_SWING_LIQUIDITY_PROXY_RULE,
            ),
        }
        return output


class LiquiditySweepFlowDecisionAreaEngine(FlowValidatedDecisionAreaEngine):
    """First-return engine over liquidity-taking, flow-valid 15-minute OBs."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = LiquiditySweepFlowValidatedOBBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )


class EasyChartRE1SweepFlowOBBundle(EasyChartRE1PhaseFlowOBBundle):
    """Ordered-channel core plus complete liquidity-take OB auctions."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.flow_decision_ob = LiquiditySweepFlowDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["flow_decision_ob"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["liquidity_sweep_flow_ob_policy"] = {
            "formation": self.flow_decision_ob.formation_flow_diagnostics,
            "rules": (
                LIQUIDITY_TAKING_OB_RULE,
                CAUSAL_SWING_LIQUIDITY_PROXY_RULE,
                FLOW_VALIDATED_OB_FORMATION_RULE,
                FLOW_OB_FIRST_TOUCH_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1SweepFlowOBBundle
