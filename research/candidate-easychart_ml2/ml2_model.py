"""CatBoost probability model and compounding-aligned utility for EasyChart ML2.

EasyChart fixes entry, stop and target before entry. The model estimates the
probability that the target is touched before the stop. Selection uses expected
log NAV growth at the project's immutable 3% risk, after configured costs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from ml1_model import TradeEconomics


MODEL_SCHEMA = "easychart_ml2_catboost_binary_v1"
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
class ML2Decision:
    raw_probability: float
    target_probability: float
    tree_probability_std: float
    expected_net_r: float
    expected_log_growth: float
    win_log_growth: float
    loss_log_growth: float
    required_probability: float
    accepted: bool
    reason: str


class CatBoostProbabilityModel:
    """Load a checksum-bound CatBoost model plus disjoint Platt calibration."""

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
        expected_net_r = economics.expected_net_r(probability)
        win_multiplier = 1.0 + self.risk_fraction * economics.win_net_r
        loss_multiplier = 1.0 + self.risk_fraction * economics.loss_net_r

        if economics.win_net_r <= 0.0:
            accepted = False
            reason = "NONPOSITIVE_POST_COST_WIN"
            win_log = loss_log = expected_log = float("-inf")
        elif economics.loss_net_r >= 0.0:
            accepted = False
            reason = "INVALID_POST_COST_LOSS"
            win_log = loss_log = expected_log = float("-inf")
        elif win_multiplier <= 0.0 or loss_multiplier <= 0.0:
            accepted = False
            reason = "INVALID_FIXED_RISK_NAV_MULTIPLIER"
            win_log = loss_log = expected_log = float("-inf")
        else:
            win_log = math.log(win_multiplier)
            loss_log = math.log(loss_multiplier)
            expected_log = probability * win_log + (1.0 - probability) * loss_log
            accepted = expected_log > 0.0
            reason = (
                "POSITIVE_EXPECTED_LOG_GROWTH"
                if accepted
                else "NONPOSITIVE_EXPECTED_LOG_GROWTH"
            )

        return ML2Decision(
            raw_probability=raw,
            target_probability=probability,
            tree_probability_std=0.0,
            expected_net_r=expected_net_r,
            expected_log_growth=expected_log,
            win_log_growth=win_log,
            loss_log_growth=loss_log,
            required_probability=economics.break_even_probability,
            accepted=accepted,
            reason=reason,
        )
