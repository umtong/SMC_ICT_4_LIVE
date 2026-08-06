"""Confirmed lower-timeframe swing pools inside a structurally valid HTF bias."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from hierarchical_sweep_engine import (
    HierarchicalLiquiditySweepContinuationEngine,
    _Bias,
    _SweepEpisode,
)
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(frozen=True, slots=True)
class _LiquidityPool:
    side: str
    level: float
    source_ts_ns: int
    confirmed_ts_ns: int


class HierarchicalConfirmedPoolContinuationEngine(HierarchicalLiquiditySweepContinuationEngine):
    """Replace adjacent-bucket levels with causally confirmed swing liquidity.

    A center LTF auction becomes a pool only after a later completed auction
    confirms that its low/high is a pivot. The engine then reuses the parent
    HTF acceptance, one-use level contract, opposing-flow sweep, separate
    response and Nautilus execution path. Bias expiry may be structural-only:
    a context remains valid until its accepted boundary is lost or replaced.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._liquidity_pools: list[_LiquidityPool] = []
        self._last_pool_confirmation_bar_ns: int | None = None
        self._pending_local_target: tuple[float, str] | None = None

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        before = self._liquidity_history[-1].end_ts_ns if self._liquidity_history else None
        step = super().observe(snapshot, allow_new=allow_new)
        after = self._liquidity_history[-1].end_ts_ns if self._liquidity_history else None
        if after is not None and after != before and after != self._last_pool_confirmation_bar_ns:
            self._confirm_liquidity_pools()
            self._last_pool_confirmation_bar_ns = after
        return step

    def _confirm_liquidity_pools(self) -> None:
        if len(self._liquidity_history) < 3:
            return
        left, center, right = self._liquidity_history[-3:]
        candidates: list[_LiquidityPool] = []
        if center.low < left.low and center.low <= right.low:
            candidates.append(
                _LiquidityPool(
                    side="LOWER",
                    level=center.low,
                    source_ts_ns=center.end_ts_ns,
                    confirmed_ts_ns=right.end_ts_ns,
                ),
            )
        if center.high > left.high and center.high >= right.high:
            candidates.append(
                _LiquidityPool(
                    side="UPPER",
                    level=center.high,
                    source_ts_ns=center.end_ts_ns,
                    confirmed_ts_ns=right.end_ts_ns,
                ),
            )
        existing = {(pool.side, pool.source_ts_ns) for pool in self._liquidity_pools}
        for pool in candidates:
            if (pool.side, pool.source_ts_ns) not in existing:
                self._liquidity_pools.append(pool)
        if len(self._liquidity_pools) > 64:
            self._liquidity_pools = self._liquidity_pools[-64:]

    def _advance_bias(self, snapshot: PrimitiveSnapshot) -> ScenarioStep:
        mode = str(self.params.get("hsp_bias_expiry_mode", "STRUCTURAL_ONLY")).upper()
        if mode == "FIXED_PERIODS":
            return super()._advance_bias(snapshot)
        if mode != "STRUCTURAL_ONLY":
            raise ValueError(f"unsupported hsp_bias_expiry_mode: {mode}")

        bias = self._bias
        assert bias is not None
        observation = snapshot.observation
        if snapshot.index <= bias.created_index:
            return ScenarioStep()
        tolerance = float(self.params.get("hsc_bias_boundary_loss_atr", 0.08)) * bias.atr_htf
        if bias.direction == "LONG":
            bias.extreme = max(bias.extreme, observation.high)
            if observation.close < bias.boundary - tolerance:
                return self._reset_bias_and_sweep(snapshot, "BULLISH_ACCEPTED_BOUNDARY_LOST")
        else:
            bias.extreme = min(bias.extreme, observation.low)
            if observation.close > bias.boundary + tolerance:
                return self._reset_bias_and_sweep(snapshot, "BEARISH_ACCEPTED_BOUNDARY_LOST")
        return ScenarioStep()

    def _maybe_start_sweep(self, snapshot: PrimitiveSnapshot) -> ScenarioTransition | None:
        mode = str(self.params.get("hsp_liquidity_pool_mode", "CONFIRMED_SWING")).upper()
        if mode == "PREVIOUS_BUCKET":
            return super()._maybe_start_sweep(snapshot)
        if mode != "CONFIRMED_SWING":
            raise ValueError(f"unsupported hsp_liquidity_pool_mode: {mode}")

        bias = self._bias
        if bias is None or snapshot.index <= bias.created_index:
            return None
        side = "LOWER" if bias.direction == "LONG" else "UPPER"
        pools = [pool for pool in self._liquidity_pools if pool.side == side]
        pools.sort(key=lambda pool: pool.confirmed_ts_ns, reverse=True)
        if not pools:
            return None

        observation = snapshot.observation
        depth = float(self.params.get("hsc_sweep_min_atr_1m", 0.10)) * snapshot.atr
        opposing_flow = float(self.params.get("hsc_sweep_opposing_flow_ratio", 0.03))
        reclaim = float(self.params.get("hsc_sweep_reclaim_tolerance_atr_1m", 0.02)) * snapshot.atr
        use_flow = bool(self.params.get("hsc_use_flow_proxy", True))
        impulse_width = abs(bias.extreme - bias.boundary)
        if impulse_width <= 0.0:
            return None

        chosen: tuple[_LiquidityPool, float] | None = None
        for pool in pools:
            key = (pool.source_ts_ns, bias.direction)
            if key in self._consumed_levels:
                continue
            if bias.direction == "LONG":
                position = (pool.level - bias.boundary) / impulse_width
                position_ok = -0.10 <= position <= float(self.params.get("hsc_max_impulse_position", 0.70))
                swept = observation.low <= pool.level - depth
                reclaimed = observation.close >= pool.level - reclaim
                flow_ok = snapshot.flow_ratio <= -opposing_flow if use_flow else True
            else:
                position = (bias.boundary - pool.level) / impulse_width
                position_ok = -0.10 <= position <= float(self.params.get("hsc_max_impulse_position", 0.70))
                swept = observation.high >= pool.level + depth
                reclaimed = observation.close <= pool.level + reclaim
                flow_ok = snapshot.flow_ratio >= opposing_flow if use_flow else True
            if position_ok and swept and reclaimed and flow_ok:
                chosen = (pool, position)
                break
        if chosen is None:
            return None

        pool, position = chosen
        self._consumed_levels.add((pool.source_ts_ns, bias.direction))
        self._sweep_sequence += 1
        self._sweep = _SweepEpisode(
            scenario_id=f"HSP-{snapshot.observation.ts_ns}-{self._sweep_sequence:06d}",
            direction=bias.direction,
            state="COUNTER_DIRECTION_LIQUIDITY_SWEEP",
            level=pool.level,
            level_ts_ns=pool.source_ts_ns,
            started_index=snapshot.index,
            started_ts_ns=snapshot.observation.ts_ns,
            sweep_low=observation.low,
            sweep_high=observation.high,
            previous_high=observation.high,
            previous_low=observation.low,
            impulse_position=position,
            sweep_flow_ratio=snapshot.flow_ratio,
        )
        return self._sweep_transition(
            self._sweep,
            "IDLE",
            "COUNTER_DIRECTION_LIQUIDITY_SWEEP",
            "CONFIRMED_LTF_SWING_LIQUIDITY_SWEPT_AGAINST_ACCEPTED_BIAS",
            pool.level,
            {
                "direction": bias.direction,
                "bias_context_id": bias.context_id,
                "bias_boundary": bias.boundary,
                "bias_extreme": bias.extreme,
                "liquidity_period_minutes": self._liquidity_period,
                "pool_side": pool.side,
                "pool_level": pool.level,
                "pool_source_ts_ns": pool.source_ts_ns,
                "pool_confirmed_ts_ns": pool.confirmed_ts_ns,
                "impulse_position": position,
                "sweep_low": observation.low,
                "sweep_high": observation.high,
                "sweep_flow_ratio": snapshot.flow_ratio,
            },
        )

    def _nearest_target_objective(self, direction: str, entry: float) -> tuple[float, str] | None:
        side = "UPPER" if direction == "LONG" else "LOWER"
        values = [
            pool.level
            for pool in self._liquidity_pools
            if pool.side == side and ((pool.level > entry) if direction == "LONG" else (pool.level < entry))
        ]
        if not values:
            return None
        values.sort(key=lambda price: abs(price - entry))
        reason = "CONFIRMED_LTF_BUYSIDE_LIQUIDITY" if direction == "LONG" else "CONFIRMED_LTF_SELLSIDE_LIQUIDITY"
        return values[0], reason

    def _nearest_target_pool(self, direction: str, entry: float) -> float | None:
        objective = self._nearest_target_objective(direction, entry)
        return None if objective is None else objective[0]

    def _select_target(
        self,
        direction: str,
        entry: float,
        stop: float,
        candidates: list[tuple[float | None, str]],
    ) -> tuple[float, str] | None:
        if self._pending_local_target is not None:
            candidates = [self._pending_local_target, *candidates]
        return super()._select_target(direction, entry, stop, candidates)

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        self._pending_local_target = self._nearest_target_objective(sweep.direction, snapshot.observation.close)
        try:
            step = super()._emit(snapshot, bias, sweep)
        finally:
            self._pending_local_target = None
        if step.signal is None:
            return step
        details = {
            **dict(step.signal.details),
            "liquidity_pool_mode": self.params.get("hsp_liquidity_pool_mode", "CONFIRMED_SWING"),
            "bias_expiry_mode": self.params.get("hsp_bias_expiry_mode", "STRUCTURAL_ONLY"),
        }
        signal: ScenarioSignal = replace(step.signal, family="HSP", details=details)
        return ScenarioStep(transitions=step.transitions, signal=signal)
