"""Candidate 05 v40: multi-cause auction router on the mature v38 path.

v38 contributes an independently detected isolated cross-asset liquidity event;
v26 contributes local sweep/retest and balance-acceptance paths.  Every final
entry is routed through the same mutually exclusive positioning state used by
v39.  The strategy does not combine scores and never changes risk size.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import strategy_v38_isolated_smt_reversal as _v38
from strategy_v39_dual_auction import _extract_side, _finite


def _concrete_strategy_class() -> type:
    candidates = [
        value
        for value in vars(_v38).values()
        if isinstance(value, type)
        and value.__module__ == _v38.__name__
        and value.__name__.endswith("Strategy")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one concrete v38 strategy, found {[c.__name__ for c in candidates]}",
        )
    return candidates[0]


_BASE = _concrete_strategy_class()


class MultiCauseAuctionRouterStrategy(_BASE):
    """Route all completed causal entries through one positioning transition."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "v40_entry_attempts": 0,
                "v40_deleveraging_reversal_pass": 0,
                "v40_position_building_pass": 0,
                "v40_crowded_exhaustion_pass": 0,
                "v40_auction_reject": 0,
                "v40_missing_state_reject": 0,
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

    @staticmethod
    def _log_ratio(value: float) -> float:
        return math.log(value) if math.isfinite(value) and value > 0.0 else math.nan

    def _auction_permission(self, side: int) -> str | None:
        oi_change = self._value("oi_change_15m")
        flow_15s = side * self._value("flow_15s")
        flow_60s = side * self._value("flow_60s")
        flow_3m = side * self._value("flow_3m")
        depth = side * self._value("depth_imbalance_1")
        efficiency = self._value("efficiency_60s")
        burst = self._value("notional_burst")
        taker = side * self._log_ratio(self._value("sum_taker_long_short_vol_ratio"))
        crowd = side * self._log_ratio(self._value("count_long_short_ratio"))
        top = side * self._log_ratio(self._value("sum_toptrader_long_short_ratio"))

        core = (oi_change, flow_15s, flow_60s, depth, efficiency, burst)
        if not all(math.isfinite(value) for value in core):
            self.diagnostics["v40_missing_state_reject"] += 1
            return None

        deleveraging_reversal = (
            oi_change <= 0.0
            and flow_15s > 0.0
            and flow_15s > flow_60s
            and depth > 0.0
            and efficiency >= 0.10
            and burst >= 1.0
        )
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
        # A local or SMT reversal may also be sponsored by informed positioning
        # while the broad crowd and taker flow still press the exhausted side.
        crowded_exhaustion = (
            oi_change <= 0.0
            and all(math.isfinite(value) for value in (taker, crowd, top))
            and flow_15s > 0.0
            and depth > 0.0
            and efficiency >= 0.10
            and top > crowd
            and taker <= 0.0
        )

        states = [
            ("DELEVERAGING_REVERSAL", deleveraging_reversal),
            ("POSITION_BUILDING_ACCEPTANCE", position_building),
            ("CROWDED_EXHAUSTION_REVERSAL", crowded_exhaustion),
        ]
        passed = [name for name, value in states if value]
        if not passed:
            self.diagnostics["v40_auction_reject"] += 1
            return None
        # The two reversal descriptions may coincide and represent the same
        # economic state; continuation is sign-separated by OI and cannot.
        if "POSITION_BUILDING_ACCEPTANCE" in passed and len(passed) > 1:
            self.diagnostics["v40_auction_reject"] += 1
            return None
        state = passed[0]
        key = {
            "DELEVERAGING_REVERSAL": "v40_deleveraging_reversal_pass",
            "POSITION_BUILDING_ACCEPTANCE": "v40_position_building_pass",
            "CROWDED_EXHAUSTION_REVERSAL": "v40_crowded_exhaustion_pass",
        }[state]
        self.diagnostics[key] += 1
        return state

    def _reject_entry_state(self) -> None:
        for name in (
            "pending",
            "armed_entry_path",
            "balance_acceptance_watch",
            "confirmed_second_touch_watch",
            "isolated_smt_watch",
            "isolated_smt_setup",
        ):
            if not hasattr(self, name):
                continue
            value = getattr(self, name)
            if isinstance(value, list):
                value.clear()
            else:
                setattr(self, name, None)
        if hasattr(self, "entry_pending"):
            self.entry_pending = False


def _wrap_entry_submit(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(self: MultiCauseAuctionRouterStrategy, *args: Any, **kwargs: Any) -> Any:
        side = _extract_side(args, kwargs)
        if side is None:
            return original(self, *args, **kwargs)
        self.diagnostics["v40_entry_attempts"] += 1
        if self._auction_permission(side) is None:
            self._reject_entry_state()
            return False
        return original(self, *args, **kwargs)
    wrapped.__name__ = name
    wrapped.__qualname__ = f"MultiCauseAuctionRouterStrategy.{name}"
    wrapped.__doc__ = getattr(original, "__doc__", None)
    return wrapped


_WRAPPED: list[str] = []
for _name in dir(_BASE):
    if not (_name.startswith("_submit") and "entry" in _name):
        continue
    _method = getattr(_BASE, _name)
    if callable(_method):
        setattr(MultiCauseAuctionRouterStrategy, _name, _wrap_entry_submit(_name, _method))
        _WRAPPED.append(_name)
if not _WRAPPED:
    raise RuntimeError("v40 did not locate inherited entry-submission helpers")

CandidateStrategy = MultiCauseAuctionRouterStrategy
StrategyClass = MultiCauseAuctionRouterStrategy
