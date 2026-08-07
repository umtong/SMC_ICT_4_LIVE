"""Candidate 05 v46: pre-evaluation selective evidence over v26 execution.

The model is trained once on chronologically earlier NautilusTrader trades and
validated on a later period which still precedes every frozen evaluation week.
It selects entries only; every admitted trade retains the inherited structural
stop, real liquidity target, fees, adverse slippage, 3% current-NAV risk and
NautilusTrader order/account lifecycle.
"""
from __future__ import annotations

import inspect
import json
import math
import os
from pathlib import Path
from typing import Any, Callable

import strategy_v26 as _v26
from train_v46_evidence_model import FEATURE_NAMES


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


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _exact_side(value: Any) -> int | None:
    if value is None:
        return None
    if hasattr(value, "side"):
        result = _exact_side(getattr(value, "side"))
        if result is not None:
            return result
    text = str(value).upper()
    if text in {"BUY", "LONG", "1", "1.0", "ORDERSIDE.BUY"}:
        return 1
    if text in {"SELL", "SHORT", "-1", "-1.0", "ORDERSIDE.SELL"}:
        return -1
    if isinstance(value, (int, float)) and float(value) in (-1.0, 1.0):
        return int(float(value))
    return None


def _bound_values(original: Callable[..., Any], self: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        bound = inspect.signature(original).bind_partial(self, *args, **kwargs)
        return dict(bound.arguments)
    except TypeError:
        return {"args": args, **kwargs}


def _entry_side(bound: dict[str, Any]) -> int | None:
    for name in ("side", "order_side", "entry_side"):
        result = _exact_side(bound.get(name))
        if result is not None:
            return result
    for name in ("setup", "pending", "path", "watch", "candidate"):
        result = _exact_side(bound.get(name))
        if result is not None:
            return result
    for value in bound.values():
        if hasattr(value, "side"):
            result = _exact_side(value)
            if result is not None:
                return result
    return None


def _details(bound: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in bound.values():
        if isinstance(value, dict):
            merged.update(value)
        details = getattr(value, "details", None)
        if isinstance(details, dict):
            merged.update(details)
        for name in (
            "branch",
            "penetration_atr",
            "pool_age_minutes",
            "target_net_r",
            "expected_net_r",
            "minimum_net_r",
        ):
            if hasattr(value, name):
                merged[name] = getattr(value, name)
    return merged


def _log_feature(value: float) -> float:
    return math.log1p(max(value, 0.0)) if math.isfinite(value) else math.nan


class SelectiveEvidenceStrategy(_BASE):
    """Admit only entries supported by the frozen pre-evaluation model."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        path = Path(
            os.environ.get(
                "CANDIDATE05_V46_MODEL",
                str(Path(__file__).with_name("v46_model.json")),
            ),
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "candidate-05-v46-selective-evidence-v1":
            raise RuntimeError("invalid v46 model schema")
        if payload.get("validation_pass") is not True:
            raise RuntimeError("v46 pre-evaluation validation did not pass")
        model = payload["model"]
        if tuple(model["feature_names"]) != FEATURE_NAMES:
            raise RuntimeError("v46 model feature contract mismatch")
        self.v46_means = tuple(float(value) for value in model["means"])
        self.v46_scales = tuple(float(value) for value in model["scales"])
        self.v46_weights = tuple(float(value) for value in model["weights"])
        self.v46_intercept = float(model["intercept"])
        self.v46_threshold = float(model["threshold"])
        self.diagnostics.update(
            {
                "v46_entry_attempts": 0,
                "v46_selected_entries": 0,
                "v46_rejected_entries": 0,
                "v46_side_unresolved": 0,
                "v46_max_probability": 0.0,
                "v46_min_selected_probability": 1.0,
            },
        )

    def _fv(self, name: str) -> float:
        try:
            return _number(self._feature(name))
        except Exception:
            current = getattr(self, "current_feature", None)
            return _number(current.get(name)) if isinstance(current, dict) else math.nan

    @staticmethod
    def _branch_flags(helper_name: str, details: dict[str, Any]) -> tuple[float, float, float, float, float]:
        branch = f"{helper_name} {details.get('branch', '')}".lower()
        return (
            float("sponsor" in branch or "choch" in branch),
            float("retrace" in branch),
            float("second" in branch or "touch" in branch),
            float("balance" in branch or "position_build" in branch),
            float("breakaway" in branch),
        )

    def _features(self, *, side: int, helper_name: str, details: dict[str, Any]) -> tuple[float, ...]:
        flow15 = self._fv("flow_15s")
        flow60 = self._fv("flow_60s")
        flow3m = self._fv("flow_3m")
        depth = self._fv("depth_imbalance_1")
        efficiency = self._fv("efficiency_60s")
        burst = self._fv("notional_burst")
        absorption = self._fv("absorption_60s")
        oi = self._fv("oi_change_15m")
        penetration = _number(details.get("penetration_atr"))
        pool_age = _number(details.get("pool_age_minutes", details.get("pool_age_bars")))
        target_r = math.nan
        for name in ("target_net_r", "net_r", "expected_net_r", "minimum_net_r"):
            candidate = _number(details.get(name))
            if math.isfinite(candidate):
                target_r = candidate
                break
        flags = self._branch_flags(helper_name, details)
        return (
            side * flow15 if math.isfinite(flow15) else math.nan,
            side * flow60 if math.isfinite(flow60) else math.nan,
            side * flow3m if math.isfinite(flow3m) else math.nan,
            side * (flow15 - flow60) if math.isfinite(flow15) and math.isfinite(flow60) else math.nan,
            side * depth if math.isfinite(depth) else math.nan,
            efficiency,
            _log_feature(burst),
            _log_feature(absorption),
            oi,
            penetration,
            _log_feature(pool_age),
            target_r,
            *flags,
        )

    def _probability(self, values: tuple[float, ...]) -> float:
        logit = self.v46_intercept
        for value, mean, scale, weight in zip(
            values,
            self.v46_means,
            self.v46_scales,
            self.v46_weights,
            strict=True,
        ):
            filled = value if math.isfinite(value) else mean
            logit += ((filled - mean) / scale) * weight
        logit = min(max(logit, -30.0), 30.0)
        return 1.0 / (1.0 + math.exp(-logit))

    def _clear_rejected_state(self) -> None:
        for name in (
            "pending",
            "armed_entry_path",
            "balance_acceptance_watch",
            "confirmed_second_touch_watch",
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


def _wrap(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(self: SelectiveEvidenceStrategy, *args: Any, **kwargs: Any) -> Any:
        bound = _bound_values(original, self, args, kwargs)
        side = _entry_side(bound)
        if side is None:
            self.diagnostics["v46_side_unresolved"] += 1
            return original(self, *args, **kwargs)
        self.diagnostics["v46_entry_attempts"] += 1
        probability = self._probability(
            self._features(side=side, helper_name=name, details=_details(bound)),
        )
        self.diagnostics["v46_max_probability"] = max(
            float(self.diagnostics["v46_max_probability"]),
            probability,
        )
        if probability < self.v46_threshold:
            self.diagnostics["v46_rejected_entries"] += 1
            self._clear_rejected_state()
            return False
        self.diagnostics["v46_selected_entries"] += 1
        self.diagnostics["v46_min_selected_probability"] = min(
            float(self.diagnostics["v46_min_selected_probability"]),
            probability,
        )
        return original(self, *args, **kwargs)
    wrapped.__name__ = name
    wrapped.__qualname__ = f"SelectiveEvidenceStrategy.{name}"
    wrapped.__doc__ = getattr(original, "__doc__", None)
    return wrapped


_WRAPPED: list[str] = []
for _name in dir(_BASE):
    if not (_name.startswith("_submit") and "entry" in _name):
        continue
    _method = getattr(_BASE, _name)
    if callable(_method):
        setattr(SelectiveEvidenceStrategy, _name, _wrap(_name, _method))
        _WRAPPED.append(_name)
if not _WRAPPED:
    raise RuntimeError("v46 did not find inherited entry helpers")

CandidateStrategy = SelectiveEvidenceStrategy
StrategyClass = SelectiveEvidenceStrategy
