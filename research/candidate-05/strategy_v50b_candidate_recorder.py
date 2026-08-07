"""Candidate 05 v50b recorder over actual constructed Nautilus brackets.

Inherited v26 entry helpers are allowed to construct their real Nautilus order
objects.  This recorder intercepts those objects immediately before broker
submission, extracts the completed entry/stop/target geometry, records the same
causal feature vector used by the frozen analog selector, and rejects the entry
so later independent candidates remain observable.  It creates no fill,
position, fee, margin, liquidation, PnL or NAV state.
"""
from __future__ import annotations

from typing import Any, Callable

import strategy_v26 as _v26
from v50_candidate_common import FEATURE_NAMES
from v50_candidate_common import bound_values
from v50_candidate_common import clear_rejected_state
from v50_candidate_common import feature_vector
from v50_order_capture import _orders
from v50_order_capture import bracket_geometry


def _base_class() -> type:
    found = [
        value
        for value in vars(_v26).values()
        if isinstance(value, type)
        and value.__module__ == _v26.__name__
        and value.__name__.endswith("Strategy")
    ]
    if len(found) != 1:
        raise RuntimeError(
            f"expected one v26 strategy, found {[value.__name__ for value in found]}",
        )
    return found[0]


_BASE = _base_class()


class ActualOrderCandidateRecorderStrategy(_BASE):
    """Record each fully constructed v26 bracket without submitting an entry."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.v50b_candidates: list[dict[str, Any]] = []
        self.v50b_context: dict[str, Any] | None = None
        self.v50b_orders: list[Any] = []
        self.diagnostics.update(
            {
                "v50_actual_order_candidate_count": 0,
                "v50_actual_order_geometry_failures": 0,
                "v50_actual_order_candidates": self.v50b_candidates,
                "v50_feature_names": list(FEATURE_NAMES),
            },
        )

    def submit_order_list(
        self,
        order_list: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if self.v50b_context is None:
            return super().submit_order_list(order_list, *args, **kwargs)
        self.v50b_orders.extend(_orders(order_list))
        return None

    def submit_order(
        self,
        order: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if self.v50b_context is None:
            return super().submit_order(order, *args, **kwargs)
        self.v50b_orders.extend(_orders(order))
        return None

    def _finish_actual_order_candidate(self) -> None:
        context = self.v50b_context
        if context is None:
            return
        row = self.bars[-1] if getattr(self, "bars", None) else {"ts": 0, "close": 0.0}
        geo = bracket_geometry(
            self.v50b_orders,
            fallback_entry=float(row["close"]),
        )
        if geo is None:
            self.diagnostics["v50_actual_order_geometry_failures"] += 1
        else:
            record = {
                "candidate_id": f"v50b-{len(self.v50b_candidates) + 1:08d}",
                "ts_event": int(row["ts"]),
                "helper": str(context["helper"]),
                **geo,
                "features": list(
                    feature_vector(
                        self,
                        side=int(geo["side"]),
                        helper_name=str(context["helper"]),
                        bound=dict(context["bound"]),
                        geometry_values=geo,
                    ),
                ),
            }
            self.v50b_candidates.append(record)
            self.diagnostics["v50_actual_order_candidate_count"] += 1
        clear_rejected_state(self)
        self.v50b_context = None
        self.v50b_orders.clear()


def _wrap(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(
        self: ActualOrderCandidateRecorderStrategy,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if self.v50b_context is not None:
            raise RuntimeError("nested v50b recorder context")
        self.v50b_context = {
            "helper": name,
            "bound": bound_values(original, self, args, kwargs),
        }
        self.v50b_orders.clear()
        try:
            return original(self, *args, **kwargs)
        finally:
            self._finish_actual_order_candidate()

    wrapped.__name__ = name
    wrapped.__qualname__ = f"ActualOrderCandidateRecorderStrategy.{name}"
    wrapped.__doc__ = getattr(original, "__doc__", None)
    return wrapped


_WRAPPED: list[str] = []
for _name in dir(_BASE):
    if not (_name.startswith("_submit") and "entry" in _name):
        continue
    _method = getattr(_BASE, _name)
    if callable(_method):
        setattr(
            ActualOrderCandidateRecorderStrategy,
            _name,
            _wrap(_name, _method),
        )
        _WRAPPED.append(_name)
if not _WRAPPED:
    raise RuntimeError("v50b recorder found no inherited entry helpers")


CandidateStrategy = ActualOrderCandidateRecorderStrategy
StrategyClass = ActualOrderCandidateRecorderStrategy


__all__ = [
    "ActualOrderCandidateRecorderStrategy",
    "CandidateStrategy",
    "StrategyClass",
    "_BASE",
    "_WRAPPED",
]
