"""Calibrated target-before-stop probability and fixed-risk NAV utility.

The EasyChart engine freezes direction, entry, structural stop and objective
before any order is submitted.  ML2 estimates only the probability that the
objective is touched before the stop.  Costs are converted to the same planned-R
units and selection uses expected log NAV growth at the project's immutable 3%
risk.  The model never changes position size or trade geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


MODEL_SCHEMA = "easychart_ml2_catboost_binary_v2"
_EPS = 1e-9


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        z = math.exp(-min(value, 60.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -60.0))
    return z / (1.0 + z)


def _logit(probability: float) -> float:
    p = _clip(probability, _EPS, 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TradeEconomics:
    """Frozen target and stop outcomes in units of planned structural risk."""

    planned_risk: float
    planned_reward: float
    gross_rr: float
    win_net_r: float
    loss_net_r: float
    arithmetic_break_even_probability: float
    entry_fill: float
    target_fill: float
    stop_fill: float
    estimated_win_cost_r: float
    estimated_loss_cost_r: float

    @property
    def break_even_probability(self) -> float:
        """Compatibility alias for arithmetic expected-R break-even."""

        return self.arithmetic_break_even_probability

    def expected_net_r(self, target_probability: float) -> float:
        p = _clip(float(target_probability), 0.0, 1.0)
        return p * self.win_net_r + (1.0 - p) * self.loss_net_r


@dataclass(frozen=True, slots=True)
class ML2Decision:
    raw_probability: float
    target_probability: float
    tree_probability_std: float
    expected_net_r: float
    expected_log_growth: float
    win_log_growth: float
    loss_log_growth: float
    required_probability: float
    arithmetic_break_even_probability: float
    accepted: bool
    reason: str


def estimate_trade_economics(
    *,
    side: Any,
    entry: float,
    stop: float,
    target: float,
    tick_size: float,
    entry_fee_rate: float,
    target_fee_rate: float,
    stop_fee_rate: float,
    funding_rate: float = 0.0,
    entry_slippage_ticks: int = 0,
    target_slippage_ticks: int = 0,
    stop_slippage_ticks: int = 0,
) -> TradeEconomics:
    """Estimate both immutable outcomes under explicit runner costs."""

    entry = _finite(entry)
    stop = _finite(stop)
    target = _finite(target)
    tick = abs(_finite(tick_size))
    if tick <= 0.0:
        raise ValueError("tick_size must be positive")
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if entry <= 0.0 or risk <= 0.0 or reward <= 0.0:
        raise ValueError("entry, stop and target must define positive geometry")

    side_text = str(getattr(side, "name", side)).upper()
    if side_text.endswith("LONG") or side_text == "BUY":
        sign = 1.0
        if not (stop < entry < target):
            raise ValueError("long geometry must satisfy stop < entry < target")
    elif side_text.endswith("SHORT") or side_text == "SELL":
        sign = -1.0
        if not (target < entry < stop):
            raise ValueError("short geometry must satisfy target < entry < stop")
    else:
        raise ValueError(f"unknown side {side!r}")

    slippage = (entry_slippage_ticks, target_slippage_ticks, stop_slippage_ticks)
    if min(slippage) < 0:
        raise ValueError("slippage ticks cannot be negative")
    rates = tuple(
        _finite(value)
        for value in (entry_fee_rate, target_fee_rate, stop_fee_rate, funding_rate)
    )
    if any(value < 0.0 for value in rates):
        raise ValueError("fee and funding rates cannot be negative")

    entry_fill = entry + sign * int(entry_slippage_ticks) * tick
    target_fill = target - sign * int(target_slippage_ticks) * tick
    stop_fill = stop - sign * int(stop_slippage_ticks) * tick

    win_gross_r = sign * (target_fill - entry_fill) / risk
    loss_gross_r = sign * (stop_fill - entry_fill) / risk
    entry_fee_r = abs(entry_fill) * rates[0] / risk
    target_fee_r = abs(target_fill) * rates[1] / risk
    stop_fee_r = abs(stop_fill) * rates[2] / risk
    funding_r = abs(entry_fill) * rates[3] / risk
    win_net_r = win_gross_r - entry_fee_r - target_fee_r - funding_r
    loss_net_r = loss_gross_r - entry_fee_r - stop_fee_r - funding_r
    denominator = win_net_r - loss_net_r
    arithmetic_break_even = -loss_net_r / denominator if denominator > 0.0 else 1.0

    return TradeEconomics(
        planned_risk=risk,
        planned_reward=reward,
        gross_rr=reward / risk,
        win_net_r=win_net_r,
        loss_net_r=loss_net_r,
        arithmetic_break_even_probability=_clip(arithmetic_break_even, 0.0, 1.0),
        entry_fill=entry_fill,
        target_fill=target_fill,
        stop_fill=stop_fill,
        estimated_win_cost_r=(reward / risk) - win_net_r,
        estimated_loss_cost_r=abs(loss_net_r) - 1.0,
    )


def decision_from_probability(
    probability: float,
    economics: TradeEconomics,
    *,
    risk_fraction: float,
    raw_probability: float | None = None,
    reason_prefix: str = "MODEL",
) -> ML2Decision:
    """Combine a calibrated probability with immutable trade economics."""

    p = _clip(_finite(probability, 0.5), _EPS, 1.0 - _EPS)
    raw = p if raw_probability is None else _clip(_finite(raw_probability, p), _EPS, 1.0 - _EPS)
    risk_fraction = _finite(risk_fraction)
    if not 0.0 < risk_fraction < 1.0:
        raise ValueError("risk_fraction must be within (0, 1)")

    expected_net_r = economics.expected_net_r(p)
    win_multiplier = 1.0 + risk_fraction * economics.win_net_r
    loss_multiplier = 1.0 + risk_fraction * economics.loss_net_r

    if economics.win_net_r <= 0.0:
        accepted = False
        reason = "NONPOSITIVE_POST_COST_WIN"
        win_log = loss_log = expected_log = float("-inf")
        required = 1.0
    elif economics.loss_net_r >= 0.0:
        accepted = False
        reason = "INVALID_POST_COST_LOSS"
        win_log = loss_log = expected_log = float("-inf")
        required = 1.0
    elif win_multiplier <= 0.0 or loss_multiplier <= 0.0:
        accepted = False
        reason = "INVALID_FIXED_RISK_NAV_MULTIPLIER"
        win_log = loss_log = expected_log = float("-inf")
        required = 1.0
    else:
        win_log = math.log(win_multiplier)
        loss_log = math.log(loss_multiplier)
        denominator = win_log - loss_log
        required = _clip(-loss_log / denominator, 0.0, 1.0) if denominator > 0.0 else 1.0
        expected_log = p * win_log + (1.0 - p) * loss_log
        accepted = expected_log > _EPS and p > required + _EPS
        reason = (
            f"{reason_prefix}_POSITIVE_EXPECTED_LOG_GROWTH"
            if accepted
            else f"{reason_prefix}_NONPOSITIVE_EXPECTED_LOG_GROWTH"
        )

    return ML2Decision(
        raw_probability=raw,
        target_probability=p,
        tree_probability_std=0.0,
        expected_net_r=expected_net_r,
        expected_log_growth=expected_log,
        win_log_growth=win_log,
        loss_log_growth=loss_log,
        required_probability=required,
        arithmetic_break_even_probability=economics.arithmetic_break_even_probability,
        accepted=accepted,
        reason=reason,
    )


def shadow_decision(economics: TradeEconomics, *, risk_fraction: float) -> ML2Decision:
    """Non-selectable placeholder used only to record causal shadow candidates."""

    neutral = decision_from_probability(
        0.5,
        economics,
        risk_fraction=risk_fraction,
        reason_prefix="SHADOW",
    )
    return ML2Decision(
        raw_probability=float("nan"),
        target_probability=float("nan"),
        tree_probability_std=0.0,
        expected_net_r=float("nan"),
        expected_log_growth=float("nan"),
        win_log_growth=neutral.win_log_growth,
        loss_log_growth=neutral.loss_log_growth,
        required_probability=neutral.required_probability,
        arithmetic_break_even_probability=neutral.arithmetic_break_even_probability,
        accepted=False,
        reason="SHADOW_ONLY_NO_MODEL_SELECTION",
    )


class CatBoostProbabilityModel:
    """Checksum-bound CatBoost model plus disjoint Platt calibration."""

    def __init__(self, metadata_path: str | Path) -> None:
        self.metadata_path = Path(metadata_path)
        self.document = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        if self.document.get("schema") != MODEL_SCHEMA:
            raise ValueError(
                f"unsupported model schema {self.document.get('schema')!r}; expected {MODEL_SCHEMA!r}",
            )
        self.status = str(self.document.get("status", "unknown"))
        self.model_id = str(self.document.get("model_id", "unidentified"))
        self.feature_names = tuple(str(item) for item in self.document.get("feature_names", ()))
        if not self.feature_names or len(self.feature_names) != len(set(self.feature_names)):
            raise ValueError("feature_names must be nonempty and unique")
        self.defaults = {
            str(key): _finite(value)
            for key, value in dict(self.document.get("feature_defaults", {})).items()
        }
        self.clip_ranges = {
            str(key): (_finite(value[0], -1e6), _finite(value[1], 1e6))
            for key, value in dict(self.document.get("feature_clip_ranges", {})).items()
            if isinstance(value, Sequence) and len(value) == 2
        }
        self.risk_fraction = _finite(self.document.get("risk_fraction"), 0.03)
        if not 0.0 < self.risk_fraction < 1.0:
            raise ValueError("risk_fraction must be within (0, 1)")
        self.calibration = dict(self.document.get("calibration", {}))
        if self.calibration.get("kind") not in {"identity", "platt_logit"}:
            raise ValueError("calibration kind must be identity or platt_logit")

        model_file = Path(str(self.document.get("model_file", "")))
        if not model_file.name:
            raise ValueError("model_file is missing")
        self.model_path = model_file if model_file.is_absolute() else self.metadata_path.parent / model_file
        if not self.model_path.exists():
            raise FileNotFoundError(self.model_path)
        expected_sha = str(self.document.get("model_sha256", ""))
        actual_sha = sha256_file(self.model_path)
        if expected_sha and actual_sha != expected_sha:
            raise RuntimeError(
                f"CatBoost model checksum mismatch: metadata={expected_sha} actual={actual_sha}",
            )

        try:
            from catboost import CatBoostClassifier
        except ImportError as exc:
            raise RuntimeError(
                "ML2 select mode requires catboost; install requirements-ml.txt",
            ) from exc
        self._model = CatBoostClassifier()
        self._model.load_model(str(self.model_path), format="cbm")

    @staticmethod
    def stable_id(document: Mapping[str, Any]) -> str:
        payload = dict(document)
        payload.pop("model_id", None)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def assert_selectable(self) -> None:
        if self.status != "trained":
            raise RuntimeError(
                f"model {self.model_id!r} has status {self.status!r}; select mode requires trained",
            )

    def vector(self, features: Mapping[str, Any]) -> list[float]:
        values: list[float] = []
        for name in self.feature_names:
            default = self.defaults.get(name, 0.0)
            value = _finite(features.get(name, default), default)
            lower, upper = self.clip_ranges.get(name, (-1e6, 1e6))
            values.append(_clip(value, lower, upper))
        return values

    def raw_probability(self, features: Mapping[str, Any]) -> float:
        probability = self._model.predict_proba([self.vector(features)])[0][1]
        return _clip(_finite(probability, 0.5), _EPS, 1.0 - _EPS)

    def calibrate(self, raw_probability: float) -> float:
        raw = _clip(_finite(raw_probability, 0.5), _EPS, 1.0 - _EPS)
        kind = str(self.calibration.get("kind", "identity"))
        if kind == "identity":
            return raw
        coefficient = _finite(self.calibration.get("coefficient"), 1.0)
        intercept = _finite(self.calibration.get("intercept"), 0.0)
        return _clip(_sigmoid(coefficient * _logit(raw) + intercept), _EPS, 1.0 - _EPS)

    def probability(self, features: Mapping[str, Any]) -> float:
        return self.calibrate(self.raw_probability(features))

    def decide(self, features: Mapping[str, Any], economics: TradeEconomics) -> ML2Decision:
        raw = self.raw_probability(features)
        probability = self.calibrate(raw)
        return decision_from_probability(
            probability,
            economics,
            risk_fraction=self.risk_fraction,
            raw_probability=raw,
            reason_prefix="MODEL",
        )
