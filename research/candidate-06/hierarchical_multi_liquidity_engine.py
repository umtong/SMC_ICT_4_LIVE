"""Confirmed swing and equal-high/low liquidity pools under HTF bias."""

from __future__ import annotations

from dataclasses import replace
from statistics import median
from typing import Any, Mapping

from hierarchical_flow_factor_engine import HierarchicalFlowFactorizedEngine
from hierarchical_pool_engine import _LiquidityPool
from hierarchical_sweep_engine import _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


class HierarchicalMultiLiquidityEngine(HierarchicalFlowFactorizedEngine):
    """Add causally confirmed equal highs/lows without weakening flow stages.

    Strict LTF pivots and repeated highs/lows represent different liquidity
    formation mechanisms. A swing pool needs a later completed bar to confirm
    the pivot. An equal pool needs a second completed LTF auction, sufficiently
    separated in time, to hold near the first extreme and close away from it.
    Both are invisible before confirmation and consumed at most once.
    """

    def __init__(self, params: Mapping[str, Any]):
        super().__init__(params)
        self._pool_kinds: dict[tuple[str, int], str] = {}
        self._pool_touches: dict[tuple[str, int], int] = {}

    @staticmethod
    def _pool_key(pool: _LiquidityPool) -> tuple[str, int]:
        return pool.side, pool.source_ts_ns

    def _confirm_liquidity_pools(self) -> None:
        mode = str(self.params.get("hml_pool_families", "SWING_AND_EQUAL")).upper()
        if mode not in {"SWING_AND_EQUAL", "SWING_ONLY", "EQUAL_ONLY"}:
            raise ValueError(f"unsupported hml_pool_families: {mode}")

        if mode in {"SWING_AND_EQUAL", "SWING_ONLY"}:
            before = {self._pool_key(pool) for pool in self._liquidity_pools}
            super()._confirm_liquidity_pools()
            for pool in self._liquidity_pools:
                key = self._pool_key(pool)
                if key not in before or key not in self._pool_kinds:
                    self._pool_kinds.setdefault(key, "CONFIRMED_SWING")
                    self._pool_touches.setdefault(key, 1)

        if mode in {"SWING_AND_EQUAL", "EQUAL_ONLY"}:
            self._confirm_equal_pools()

        if len(self._liquidity_pools) > 128:
            retained = self._liquidity_pools[-128:]
            retained_keys = {self._pool_key(pool) for pool in retained}
            self._liquidity_pools = retained
            self._pool_kinds = {
                key: value for key, value in self._pool_kinds.items() if key in retained_keys
            }
            self._pool_touches = {
                key: value for key, value in self._pool_touches.items() if key in retained_keys
            }

    def _confirm_equal_pools(self) -> None:
        history = self._liquidity_history
        lookback = int(self.params.get("hml_equal_lookback_bars", 8))
        minimum_gap = int(self.params.get("hml_equal_min_intervening_bars", 1))
        if len(history) < minimum_gap + 2:
            return

        current_index = len(history) - 1
        current = history[current_index]
        recent_ranges = [bar.candle_range for bar in history[-min(len(history), lookback + 1):]]
        baseline_range = median(recent_ranges) if recent_ranges else 0.0
        tolerance = max(
            baseline_range * float(self.params.get("hml_equal_tolerance_range_fraction", 0.08)),
            abs(current.close) * 1e-7,
        )
        rejection_floor = float(self.params.get("hml_equal_rejection_close_fraction", 0.35))
        earliest = max(0, current_index - lookback)
        prior_candidates = [
            (index, history[index])
            for index in range(earliest, current_index)
            if current_index - index >= minimum_gap + 1
        ]

        lower_match = next(
            (
                bar
                for _, bar in reversed(prior_candidates)
                if abs(bar.low - current.low) <= tolerance
                and bar.close_location >= rejection_floor
                and current.close_location >= rejection_floor
            ),
            None,
        )
        if lower_match is not None:
            self._append_equal_pool(
                side="LOWER",
                level=min(lower_match.low, current.low),
                prior_ts_ns=lower_match.end_ts_ns,
                current_ts_ns=current.end_ts_ns,
                tolerance=tolerance,
            )

        upper_match = next(
            (
                bar
                for _, bar in reversed(prior_candidates)
                if abs(bar.high - current.high) <= tolerance
                and bar.close_location <= 1.0 - rejection_floor
                and current.close_location <= 1.0 - rejection_floor
            ),
            None,
        )
        if upper_match is not None:
            self._append_equal_pool(
                side="UPPER",
                level=max(upper_match.high, current.high),
                prior_ts_ns=upper_match.end_ts_ns,
                current_ts_ns=current.end_ts_ns,
                tolerance=tolerance,
            )

    def _append_equal_pool(
        self,
        *,
        side: str,
        level: float,
        prior_ts_ns: int,
        current_ts_ns: int,
        tolerance: float,
    ) -> None:
        direction = "LONG" if side == "LOWER" else "SHORT"
        for pool in reversed(self._liquidity_pools):
            if pool.side != side or abs(pool.level - level) > tolerance:
                continue
            key = self._pool_key(pool)
            if (pool.source_ts_ns, direction) not in self._consumed_levels:
                self._pool_touches[key] = max(self._pool_touches.get(key, 1), 2)
                return
            if prior_ts_ns <= pool.confirmed_ts_ns:
                return
            break

        pool = _LiquidityPool(
            side=side,
            level=level,
            source_ts_ns=current_ts_ns,
            confirmed_ts_ns=current_ts_ns,
        )
        self._liquidity_pools.append(pool)
        key = self._pool_key(pool)
        self._pool_kinds[key] = "EQUAL_LOW" if side == "LOWER" else "EQUAL_HIGH"
        self._pool_touches[key] = 2

    def _maybe_start_sweep(self, snapshot: PrimitiveSnapshot) -> ScenarioTransition | None:
        transition = super()._maybe_start_sweep(snapshot)
        if transition is None or self._sweep is None:
            return transition
        key = ("LOWER" if self._sweep.direction == "LONG" else "UPPER", self._sweep.level_ts_ns)
        kind = self._pool_kinds.get(key, "CONFIRMED_SWING")
        touches = self._pool_touches.get(key, 1)
        reason = (
            "CONFIRMED_EQUAL_LTF_LIQUIDITY_SWEPT_AGAINST_ACCEPTED_BIAS"
            if kind.startswith("EQUAL_")
            else transition.reason_code
        )
        return replace(
            transition,
            reason_code=reason,
            details={**dict(transition.details), "pool_kind": kind, "pool_touches": touches},
        )

    def _nearest_target_objective(self, direction: str, entry: float) -> tuple[float, str] | None:
        side = "UPPER" if direction == "LONG" else "LOWER"
        pools = [
            pool
            for pool in self._liquidity_pools
            if pool.side == side and ((pool.level > entry) if direction == "LONG" else (pool.level < entry))
        ]
        pools.sort(key=lambda pool: abs(pool.level - entry))
        if not pools:
            return None
        pool = pools[0]
        kind = self._pool_kinds.get(self._pool_key(pool), "CONFIRMED_SWING")
        if direction == "LONG":
            reason = "EQUAL_LTF_BUYSIDE_LIQUIDITY" if kind == "EQUAL_HIGH" else "CONFIRMED_LTF_BUYSIDE_LIQUIDITY"
        else:
            reason = "EQUAL_LTF_SELLSIDE_LIQUIDITY" if kind == "EQUAL_LOW" else "CONFIRMED_LTF_SELLSIDE_LIQUIDITY"
        return pool.level, reason

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        step = super()._emit(snapshot, bias, sweep)
        if step.signal is None:
            return step
        key = ("LOWER" if sweep.direction == "LONG" else "UPPER", sweep.level_ts_ns)
        details = {
            **dict(step.signal.details),
            "swept_pool_kind": self._pool_kinds.get(key, "CONFIRMED_SWING"),
            "swept_pool_touches": self._pool_touches.get(key, 1),
            "pool_family_contract": self.params.get("hml_pool_families", "SWING_AND_EQUAL"),
        }
        signal: ScenarioSignal = replace(step.signal, family="HML", details=details)
        return ScenarioStep(transitions=step.transitions, signal=signal)
