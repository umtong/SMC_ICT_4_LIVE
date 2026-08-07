"""Candidate 05 v39: causal dual-auction gate over the mature v26 execution path.

The inherited v26 state machine owns market structure, order creation, fees,
slippage, quantity, contingent lifecycle, positions, portfolio and NAV.  This
module changes only the final causal permission to submit a new-entry order.
A setup must be explained by exactly one of two economically distinct states:

1. deleveraging reversal: OI contracts while the completed 15-second flow,
   displayed depth and realised price response have turned with the proposed
   reversal; or
2. position-building acceptance: OI expands while completed 15-second,
   60-second and three-minute flow, depth and price efficiency remain aligned
   with the proposed continuation.

No score, confidence multiplier, parameter search, or risk adjustment is used.
"""
from __future__ import annotations

import inspect
import math
from typing import Any, Callable

import strategy_v26 as _v26


def _concrete_strategy_class() -> type:
    candidates = [
        value
        for value in vars(_v26).values()
        if isinstance(value, type)
        and value.__module__ == _v26.__name__
        and value.__name__.endswith("Strategy")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one concrete v26 strategy, found {[c.__name__ for c in candidates]}",
        )
    return candidates[0]


_BASE = _concrete_strategy_class()


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _side_from(value: Any) -> int | None:
    if value is None:
        return None
    if hasattr(value, "side"):
        result = _side_from(getattr(value, "side"))
        if result is not None:
            return result
    text = str(value).upper()
    if text in {"1", "1.0", "BUY", "LONG", "ORDERSIDE.BUY"}:
        return 1
    if text in {"-1", "-1.0", "SELL", "SHORT", "ORDERSIDE.SELL"}:
        return -1
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return 1 if number > 0 else -1 if number < 0 else None


def _extract_side(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | None:
    for key in ("side", "order_side", "entry_side"):
        if key in kwargs:
            result = _side_from(kwargs[key])
            if result is not None:
                return result
    for value in args:
        result = _side_from(value)
        if result is not None:
            return result
    return None


class DualAuctionStateStrategy(_BASE):
    """v26 execution with a mutually exclusive causal auction permission."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "v39_entry_attempts": 0,
                "v39_deleveraging_reversal_pass": 0,
                "v39_position_building_pass": 0,
                "v39_incoherent_auction_reject": 0,
                "v39_missing_state_reject": 0,
            },
        )

    def _value(self, name: str) -> float:
        feature = getattr(self, "_feature", None)
        if callable(feature):
            try:
                return _finite(feature(name))
            except Exception:
                return math.nan
        current = getattr(self, "current_feature", None)
        if isinstance(current, dict):
            return _finite(current.get(name))
        return math.nan

    def _auction_permission(self, side: int) -> str | None:
        oi_change = self._value("oi_change_15m")
        flow_15s = side * self._value("flow_15s")
        flow_60s = side * self._value("flow_60s")
        flow_3m = side * self._value("flow_3m")
        depth = side * self._value("depth_imbalance_1")
        efficiency = self._value("efficiency_60s")
        burst = self._value("notional_burst")

        required = (oi_change, flow_15s, flow_60s, depth, efficiency, burst)
        if not all(math.isfinite(value) for value in required):
            self.diagnostics["v39_missing_state_reject"] += 1
            return None

        # Existing positions are being forced out: broad flow may still reflect
        # the raid, but the most recent completed tail, book and realised price
        # response must already have turned with the proposed reversal.
        deleveraging_reversal = (
            oi_change <= 0.0
            and flow_15s > 0.0
            and flow_15s > flow_60s
            and depth > 0.0
            and efficiency >= 0.10
            and burst >= 1.0
        )

        # New positions are sponsoring accepted price discovery: all available
        # completed horizons, realised impact and the resting book must agree.
        position_building = (
            oi_change > 0.0
            and math.isfinite(flow_3m)
            and flow_15s > 0.0
            and flow_60s > 0.0
            and flow_3m >= 0.0
            and depth > 0.0
            and efficiency >= 0.15
            and burst >= 1.0
        )

        if deleveraging_reversal and not position_building:
            self.diagnostics["v39_deleveraging_reversal_pass"] += 1
            return "DELEVERAGING_REVERSAL"
        if position_building and not deleveraging_reversal:
            self.diagnostics["v39_position_building_pass"] += 1
            return "POSITION_BUILDING_ACCEPTANCE"
        self.diagnostics["v39_incoherent_auction_reject"] += 1
        return None

    def _reject_entry_state(self, reason: str) -> None:
        # Submission helpers are called only after the scenario has completed.
        # Refusing permission must therefore release observational state without
        # manufacturing an order, fill, fee or PnL event.
        for name in (
            "pending",
            "armed_entry_path",
            "balance_acceptance_watch",
            "confirmed_second_touch_watch",
        ):
            if hasattr(self, name):
                value = getattr(self, name)
                if isinstance(value, list):
                    value.clear()
                else:
                    setattr(self, name, None)
        if hasattr(self, "entry_pending"):
            self.entry_pending = False
        self.diagnostics[reason] = int(self.diagnostics.get(reason, 0)) + 1


def _wrap_entry_submit(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(self: DualAuctionStateStrategy, *args: Any, **kwargs: Any) -> Any:
        side = _extract_side(args, kwargs)
        if side is None:
            # Do not interfere with a helper whose signature is unrelated to a
            # directional new entry.
            return original(self, *args, **kwargs)
        self.diagnostics["v39_entry_attempts"] += 1
        state = self._auction_permission(side)
        if state is None:
            self._reject_entry_state("v39_entry_permission_rejected")
            return False
        return original(self, *args, **kwargs)

    wrapped.__name__ = name
    wrapped.__qualname__ = f"DualAuctionStateStrategy.{name}"
    wrapped.__doc__ = getattr(original, "__doc__", None)
    return wrapped


# v26 has several branch-specific entry helpers.  Wrap every inherited private
# helper explicitly named as an entry submission, while leaving exits, bracket
# children and all NautilusTrader accounting untouched.
_WRAPPED: list[str] = []
for _name in dir(_BASE):
    if not (_name.startswith("_submit") and "entry" in _name):
        continue
    _method = getattr(_BASE, _name)
    if not callable(_method):
        continue
    setattr(DualAuctionStateStrategy, _name, _wrap_entry_submit(_name, _method))
    _WRAPPED.append(_name)

if not _WRAPPED:
    raise RuntimeError("v39 did not locate any inherited entry-submission helper")


# Stable aliases for the existing runner and shared-account variant registry.
CandidateStrategy = DualAuctionStateStrategy
StrategyClass = DualAuctionStateStrategy
