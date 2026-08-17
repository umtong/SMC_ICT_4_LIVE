"""Causal common-factor history and inherited pre-plan context diagnostics.

RE1's final router is not the only place where the live four-market common
factor was consulted.  Some continuation engines consumed an opposing first
touch (and one OB continuation engine also suppressed formation) before a
complete plan could be emitted.  ML2 deliberately exposes those structurally
complete counterfactuals to the selector, but records the exact factor state
that the inherited policy would have seen at setup formation and immediately
before the response entry.

The transition book stores only changes in the active factor side.  It therefore
has negligible memory cost even in long runs and answers historical lookups
without using future state.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any


NS_PER_MINUTE = 60_000_000_000


def _side_name(value: Any) -> str | None:
    if value is None:
        return None
    side = getattr(value, "side", value)
    raw = getattr(side, "name", side)
    text = str(raw).upper()
    if text.endswith("LONG") or text == "BUY":
        return "LONG"
    if text.endswith("SHORT") or text == "SELL":
        return "SHORT"
    raise ValueError(f"unknown side {value!r}")


def factor_opposes(state: Any, plan_side: Any) -> bool:
    state_side = _side_name(state)
    return state_side is not None and state_side != _side_name(plan_side)


def factor_side_name(state: Any) -> str | None:
    return _side_name(state)


@dataclass(slots=True)
class FactorTransitionBook:
    """Piecewise-constant causal common-factor state."""

    _times: list[int] = field(default_factory=list)
    _states: list[Any | None] = field(default_factory=list)
    _last_side: str | None = None
    _initialized: bool = False

    def observe(self, time_ns: int, state: Any | None) -> None:
        timestamp = int(time_ns)
        if self._times and timestamp < self._times[-1]:
            raise RuntimeError(
                f"non-monotonic factor transition time {timestamp} < {self._times[-1]}",
            )
        side = factor_side_name(state)
        if self._initialized and side == self._last_side:
            return
        if self._times and timestamp == self._times[-1]:
            self._states[-1] = state
        else:
            self._times.append(timestamp)
            self._states.append(state)
        self._last_side = side
        self._initialized = True

    def state_at(self, time_ns: int) -> Any | None:
        if not self._times:
            return None
        index = bisect_right(self._times, int(time_ns)) - 1
        return None if index < 0 else self._states[index]

    @property
    def transitions(self) -> int:
        return len(self._times)


def pre_response_time_ns(plan: Any) -> int:
    observed = int(getattr(plan, "observed_time_ns", 0) or 0)
    trigger_minutes = max(
        1,
        int(float(getattr(plan, "trigger_timeframe_minutes", 1) or 1)),
    )
    return max(0, observed - trigger_minutes * NS_PER_MINUTE)


def plan_factor_snapshots(
    plan: Any,
    book: FactorTransitionBook,
) -> tuple[Any | None, Any | None]:
    setup_time = int(getattr(plan, "setup_observed_time_ns", 0) or 0)
    return book.state_at(setup_time), book.state_at(pre_response_time_ns(plan))


def _plan_text(plan: Any) -> str:
    # ``rule_provenance`` is intentionally excluded.  The underlying v5
    # contract records a process-global curriculum tuple, so parsing it would
    # make unrelated plans appear to belong to every imported scenario family.
    # Only fields frozen on this specific plan may identify the hidden veto
    # which the inherited shadow policy would have applied.
    values = (
        getattr(plan, "family", ""),
        getattr(plan, "scale_name", ""),
        getattr(plan, "scenario_path", ""),
        getattr(plan, "higher_zone_kind", ""),
        getattr(plan, "lower_zone_kind", ""),
        getattr(plan, "trigger_zone_kind", ""),
        getattr(plan, "target_zone_kind", ""),
    )
    return "|".join(str(getattr(value, "value", value)).upper() for value in values)


def inherited_preplan_factor_allows(
    plan: Any,
    book: FactorTransitionBook,
) -> bool:
    """Whether RE1's hidden engine-level factor checks would have kept a plan.

    This is used only by ML2 shadow routing.  Select mode is free to learn that
    an apparently opposing broad shock was locally absorbed, because the factor
    states themselves remain decision-time features.
    """

    text = _plan_text(plan)
    setup_state, pre_response_state = plan_factor_snapshots(plan, book)
    side = getattr(plan, "side")

    # The local OB continuation checked the common factor both when the 5m
    # footprint was born/finalized and when its first later touch occurred.
    local_ob_continuation = (
        "FACTOR_CONTINUATION_5M_OB_FIRST_RETURN" in text
        or "LOCAL_AUCTION_CONTINUATION" in text
    )
    if local_ob_continuation:
        return not (
            factor_opposes(setup_state, side)
            or factor_opposes(pre_response_state, side)
        )

    # Local and macro efficient-pullback engines consumed an opposing first
    # touch.  Their plan is emitted on the immediately following 1m response,
    # so the previous trigger close is the exact causal state to inspect.
    efficient_pullback = (
        "EFFICIENT_PULLBACK" in text
        or "MACRO_TREND_PULLBACK" in text
        or "MACRO_60M_TREND_5M_ACCEPTED_BREAK_FIRST_PULLBACK" in text
    )
    if efficient_pullback:
        return not factor_opposes(pre_response_state, side)

    return True
