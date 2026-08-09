"""Candidate 05 v47: cross-asset relative-value dislocation reversal.

BTC is the initial traded laboratory while ETH, SOL and XRP provide a dynamic
common crypto factor.  Only observations strictly earlier than the current BTC
bar are available, eliminating same-timestamp registration order.  A trade is
not taken at a residual extreme.  The residual must begin converging, BTC tail
flow and displayed depth must turn with that convergence, and the inherited
v26 state machine must still confirm local CHoCH and first retrace before any
NautilusTrader order is submitted.
"""
from __future__ import annotations

from collections import deque
import math
from statistics import median
from typing import Any

from nautilus_trader.model.data import Bar

from relative_value_context import completed_history, publish, reset
from strategy_base import PendingSetup, _as_float
from strategy_v26 import *  # noqa: F403
import strategy_v26 as _v26
from strategy_v41_competing_auction import _construct


PEERS = ("ETHUSDT", "SOLUSDT", "XRPUSDT")
RESIDUAL_WINDOW = 120
MIN_RESIDUAL_OBSERVATIONS = 60
ROBUST_Z = 3.0


def _base_class() -> type:
    found = [
        value for value in vars(_v26).values()
        if isinstance(value, type)
        and value.__module__ == _v26.__name__
        and value.__name__.endswith("Strategy")
    ]
    if len(found) != 1:
        raise RuntimeError(f"expected one v26 strategy, found {[value.__name__ for value in found]}")
    return found[0]


_BASE = _base_class()


def _symbol(instrument_id: Any) -> str:
    return str(instrument_id).split("-")[0].split(".")[0]


def _log_return(history: tuple[Any, ...], bars: int) -> float:
    if len(history) <= bars:
        return math.nan
    first = float(history[-(bars + 1)].close)
    last = float(history[-1].close)
    return math.log(last / first) if first > 0.0 and last > 0.0 else math.nan


def _robust_z(values: deque[float], current: float) -> float:
    finite = [value for value in values if math.isfinite(value)]
    if len(finite) < MIN_RESIDUAL_OBSERVATIONS:
        return math.nan
    center = median(finite)
    mad = median(abs(value - center) for value in finite)
    scale = max(1.4826 * mad, 1e-8)
    return (current - center) / scale


class RelativeValueDislocationStrategy(_BASE):
    """Arm the inherited reversal path only after residual convergence begins."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.v47_symbol = _symbol(config.instrument_id)
        self.v47_residuals: deque[float] = deque(maxlen=RESIDUAL_WINDOW)
        self.v47_previous_residual = math.nan
        self.v47_last_signal_index = -10_000
        self.diagnostics.update(
            {
                "v47_peer_context_ready": 0,
                "v47_residual_extremes": 0,
                "v47_residual_inflections": 0,
                "v47_flow_depth_confirmed": 0,
                "v47_pending_setups": 0,
                "v47_same_timestamp_peer_uses": 0,
            },
        )

    def on_start(self) -> None:
        # The shared runner creates all strategy instances before replay.  Reset
        # only once, from the BTC laboratory instance, before any bars arrive.
        if self.v47_symbol == "BTCUSDT":
            reset()
        super().on_start()

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        # Disable the inherited v26 entry detector; active pools continue to be
        # maintained and are used solely as real targets for the v47 setup.
        return

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        atr = float(self._atr()) if len(self.bars) > self.config.atr_period else math.nan
        publish(
            symbol=self.v47_symbol,
            ts=int(row["ts"]),
            close=float(row["close"]),
            atr=atr,
        )
        if self.v47_symbol == "BTCUSDT":
            self._maybe_arm_relative_value(row)

    def _peer_factor(self, ts: int) -> tuple[float, float] | None:
        returns_5m: list[float] = []
        normalized_1m: list[float] = []
        for symbol in PEERS:
            history = completed_history(symbol, before_ts=ts, count=8)
            if not history:
                return None
            if history[-1].ts >= ts:
                self.diagnostics["v47_same_timestamp_peer_uses"] += 1
                return None
            ret5 = _log_return(history, 5)
            ret1 = _log_return(history, 1)
            atr = float(history[-1].atr)
            close = float(history[-1].close)
            if not all(math.isfinite(value) for value in (ret5, ret1, atr, close)) or atr <= 0.0 or close <= 0.0:
                return None
            returns_5m.append(ret5)
            normalized_1m.append(ret1 / (atr / close))
        return median(returns_5m), median(normalized_1m)

    def _maybe_arm_relative_value(self, row: dict[str, float | int]) -> None:
        if (
            self.pending is not None
            or self.entry_pending
            or not self.portfolio.is_flat(self.config.instrument_id)
            or not self._in_evaluation(int(row["ts"]))
            or not self._features_ready(int(row["ts"]))
            or self.bar_index - self.last_entry_index < self.config.cooldown_bars
            or self.bar_index - self.v47_last_signal_index < self.config.rejection_confirmation_bars
            or len(self.bars) < max(self.config.atr_period + 2, 8)
        ):
            return
        factor = self._peer_factor(int(row["ts"]))
        if factor is None:
            return
        peer_return_5m, peer_normalized_1m = factor
        own_first = float(self.bars[-6]["close"])
        own_last = float(self.bars[-1]["close"])
        if own_first <= 0.0 or own_last <= 0.0:
            return
        own_return_5m = math.log(own_last / own_first)
        residual = own_return_5m - peer_return_5m
        z = _robust_z(self.v47_residuals, residual)
        self.diagnostics["v47_peer_context_ready"] += 1
        previous = self.v47_previous_residual
        self.v47_residuals.append(residual)
        self.v47_previous_residual = residual
        if not math.isfinite(z) or abs(z) < ROBUST_Z:
            return
        self.diagnostics["v47_residual_extremes"] += 1
        if not math.isfinite(previous) or residual == 0.0 or previous == 0.0:
            return
        if math.copysign(1.0, residual) != math.copysign(1.0, previous) or abs(residual) >= abs(previous):
            return
        self.diagnostics["v47_residual_inflections"] += 1
        side = -1 if residual > 0.0 else 1
        # The peer factor may still move against the proposed convergence, but
        # not faster than one quarter of its current one-minute ATR.
        if side * peer_normalized_1m < -0.25:
            return
        flow15 = float(self._feature("flow_15s"))
        flow60 = float(self._feature("flow_60s"))
        depth = float(self._feature("depth_imbalance_1"))
        efficiency = float(self._feature("efficiency_60s"))
        burst = float(self._feature("notional_burst"))
        if not all(math.isfinite(value) for value in (flow15, flow60, depth, efficiency, burst)):
            return
        if not (
            side * flow15 > 0.0
            and side * (flow15 - flow60) > 0.0
            and side * depth > 0.0
            and efficiency >= 0.10
            and burst >= 1.0
        ):
            return
        self.diagnostics["v47_flow_depth_confirmed"] += 1
        atr = float(self._atr())
        recent = list(self.bars)[-6:-1]
        if not recent or not math.isfinite(atr) or atr <= 0.0:
            return
        structure = (
            max(float(item["high"]) for item in recent)
            if side > 0
            else min(float(item["low"]) for item in recent)
        )
        sweep_extreme = (
            min(float(item["low"]) for item in list(self.bars)[-3:])
            if side > 0
            else max(float(item["high"]) for item in list(self.bars)[-3:])
        )
        self.scenario_counter += 1
        details = {
            "branch": "RELATIVE_VALUE_REJECTION",
            "residual": residual,
            "residual_z": z,
            "peer_return_5m": peer_return_5m,
            "peer_normalized_1m": peer_normalized_1m,
            "flow_15s": flow15,
            "flow_60s": flow60,
            "flow_3m": float(self._feature("flow_3m")),
            "efficiency_60s": efficiency,
            "notional_burst": burst,
            "depth_imbalance_1": depth,
            "oi_change_15m": float(self._feature("oi_change_15m")),
            "pool_source": "CROSS_ASSET_ROBUST_RESIDUAL",
            "pool_age_minutes": 5,
            "penetration_atr": abs(residual) / max(atr / own_last, 1e-12),
        }
        self.pending = _construct(
            PendingSetup,
            scenario_id=f"rv-{self.scenario_counter:07d}",
            branch="REJECTION",
            side=side,
            swept_kind="LOW" if side > 0 else "HIGH",
            pool_id=f"relative-value-{int(row['ts'])}",
            pool_level=float(row["close"]),
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            expires_index=self.bar_index + self.config.rejection_confirmation_bars,
            sweep_extreme=sweep_extreme,
            structure=structure,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.v47_last_signal_index = self.bar_index
        self.diagnostics["rejection_setups"] += 1
        self.diagnostics["v47_pending_setups"] += 1


CandidateStrategy = RelativeValueDislocationStrategy
StrategyClass = RelativeValueDislocationStrategy
# Existing v36 shared variant adapters can import this alias in an isolated
# worktree without changing their runner or global slot implementation.
CrossAssetRepricingGateStrategy = RelativeValueDislocationStrategy
