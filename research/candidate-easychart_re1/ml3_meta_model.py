"""Compact causal meta-model for the EasyChart RE1 ML3 router.

The deterministic EasyChart policy owns entry, stop and target geometry. This
module estimates only the probability that the already-fixed target is reached
before the already-fixed stop. A zero-drift two-barrier probability is used as
an offset; the fitted linear model therefore learns evidence that raises or
lowers the target-first odds relative to the plan's own reward/risk geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ml3_online_features import (
    CATEGORICAL_FEATURES,
    FEATURE_SCHEMA_VERSION,
    NUMERIC_FEATURES,
    OTHER_CATEGORY,
)


MODEL_FORMAT_VERSION = "easychart-re1-ml3-logistic-offset-v1"


def _finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"feature {name!r} is not numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"feature {name!r} is not finite")
    return result


def _text(value: Any, name: str) -> str:
    if value is None:
        raise ValueError(f"feature {name!r} is missing")
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        value = enum_value
    elif getattr(value, "name", None) is not None:
        value = value.name
    result = str(value)
    if not result:
        raise ValueError(f"feature {name!r} is empty")
    return result


def _sigmoid(values: np.ndarray | float) -> np.ndarray | float:
    array = np.asarray(values, dtype=float)
    clipped = np.clip(array, -35.0, 35.0)
    output = 1.0 / (1.0 + np.exp(-clipped))
    if np.ndim(values) == 0:
        return float(output)
    return output


def geometry_prior(gross_rr: float) -> float:
    """Zero-drift probability of touching target before stop."""
    rr = _finite_float(gross_rr, "gross_rr")
    if rr <= 0.0:
        raise ValueError("gross_rr must be positive")
    return 1.0 / (1.0 + rr)


def _logit(probability: float) -> float:
    p = min(max(float(probability), 1e-9), 1.0 - 1e-9)
    return math.log(p / (1.0 - p))


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _robust_location_scale(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("numeric feature has no finite training values")
    center = float(np.median(finite))
    mad = float(np.median(np.abs(finite - center)))
    scale = 1.4826 * mad
    if not math.isfinite(scale) or scale <= 1e-12:
        q25, q75 = np.percentile(finite, [25.0, 75.0])
        scale = float((q75 - q25) / 1.349)
    if not math.isfinite(scale) or scale <= 1e-12:
        scale = float(np.std(finite))
    if not math.isfinite(scale) or scale <= 1e-12:
        scale = 1.0
    return center, scale


@dataclass(slots=True)
class ML3MetaModel:
    model_format_version: str
    feature_schema_version: str
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...]
    category_levels: dict[str, tuple[str, ...]]
    numeric_centers: dict[str, float]
    numeric_scales: dict[str, float]
    coefficient_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    feature_clip: float
    l2: float
    training: dict[str, Any]
    _sha256: str = ""

    def __post_init__(self) -> None:
        if self.model_format_version != MODEL_FORMAT_VERSION:
            raise ValueError(
                f"unsupported ML3 model format {self.model_format_version!r}"
            )
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError(
                "ML3 model feature schema mismatch: "
                f"{self.feature_schema_version!r} != {FEATURE_SCHEMA_VERSION!r}"
            )
        if self.numeric_features != tuple(NUMERIC_FEATURES):
            raise ValueError("ML3 numeric feature order mismatch")
        if self.categorical_features != tuple(CATEGORICAL_FEATURES):
            raise ValueError("ML3 categorical feature order mismatch")
        if len(self.coefficient_names) != len(self.coefficients):
            raise ValueError("ML3 coefficient name/value length mismatch")
        if self.feature_clip <= 0.0 or self.l2 < 0.0:
            raise ValueError("invalid ML3 clip or regularization")
        if not math.isfinite(self.intercept):
            raise ValueError("ML3 intercept is not finite")
        if not all(math.isfinite(value) for value in self.coefficients):
            raise ValueError("ML3 coefficient is not finite")
        for name in self.numeric_features:
            if name not in self.numeric_centers or name not in self.numeric_scales:
                raise ValueError(f"missing scaler for {name}")
            if self.numeric_scales[name] <= 0.0:
                raise ValueError(f"nonpositive scale for {name}")
        for name in self.categorical_features:
            levels = self.category_levels.get(name)
            if levels is None or not levels or levels[-1] != OTHER_CATEGORY:
                raise ValueError(f"invalid category levels for {name}")

    @property
    def sha256(self) -> str:
        return self._sha256 or hashlib.sha256(_canonical_json(self.to_dict())).hexdigest()

    @property
    def feature_count(self) -> int:
        return len(self.coefficients)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_format_version": self.model_format_version,
            "feature_schema_version": self.feature_schema_version,
            "numeric_features": list(self.numeric_features),
            "categorical_features": list(self.categorical_features),
            "category_levels": {
                name: list(self.category_levels[name])
                for name in self.categorical_features
            },
            "numeric_centers": {
                name: float(self.numeric_centers[name])
                for name in self.numeric_features
            },
            "numeric_scales": {
                name: float(self.numeric_scales[name])
                for name in self.numeric_features
            },
            "coefficient_names": list(self.coefficient_names),
            "coefficients": [float(value) for value in self.coefficients],
            "intercept": float(self.intercept),
            "feature_clip": float(self.feature_clip),
            "l2": float(self.l2),
            "training": self.training,
        }

    def save(self, path: Path) -> None:
        payload = _canonical_json(self.to_dict())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        self._sha256 = hashlib.sha256(payload).hexdigest()

    @classmethod
    def load(cls, path: Path) -> "ML3MetaModel":
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        return cls(
            model_format_version=str(payload["model_format_version"]),
            feature_schema_version=str(payload["feature_schema_version"]),
            numeric_features=tuple(payload["numeric_features"]),
            categorical_features=tuple(payload["categorical_features"]),
            category_levels={
                str(name): tuple(str(value) for value in levels)
                for name, levels in payload["category_levels"].items()
            },
            numeric_centers={
                str(name): float(value)
                for name, value in payload["numeric_centers"].items()
            },
            numeric_scales={
                str(name): float(value)
                for name, value in payload["numeric_scales"].items()
            },
            coefficient_names=tuple(payload["coefficient_names"]),
            coefficients=tuple(float(value) for value in payload["coefficients"]),
            intercept=float(payload["intercept"]),
            feature_clip=float(payload["feature_clip"]),
            l2=float(payload["l2"]),
            training=dict(payload.get("training", {})),
            _sha256=hashlib.sha256(raw).hexdigest(),
        )

    def design_vector(self, features: Mapping[str, Any]) -> np.ndarray:
        values: list[float] = []
        for name in self.numeric_features:
            raw = _finite_float(features.get(name), name)
            scaled = (raw - self.numeric_centers[name]) / self.numeric_scales[name]
            values.append(float(np.clip(scaled, -self.feature_clip, self.feature_clip)))
        for name in self.categorical_features:
            observed = _text(features.get(name), name)
            known = self.category_levels[name]
            encoded = observed if observed in known else OTHER_CATEGORY
            for level in known[:-1]:
                values.append(float(encoded == level))
        vector = np.asarray(values, dtype=float)
        if vector.shape != (len(self.coefficients),):
            raise RuntimeError(
                f"ML3 design vector shape {vector.shape} != {(len(self.coefficients),)}"
            )
        return vector

    def predict_log_odds_adjustment(self, features: Mapping[str, Any]) -> float:
        vector = self.design_vector(features)
        return float(self.intercept + vector @ np.asarray(self.coefficients, dtype=float))

    def predict_probability(self, features: Mapping[str, Any]) -> float:
        prior = geometry_prior(_finite_float(features.get("gross_rr"), "gross_rr"))
        eta = _logit(prior) + self.predict_log_odds_adjustment(features)
        return float(_sigmoid(eta))

    @classmethod
    def fit(
        cls,
        feature_rows: Sequence[Mapping[str, Any]],
        labels: Sequence[int | bool | float],
        *,
        l2: float = 0.03,
        feature_clip: float = 8.0,
        maximum_iterations: int = 80,
        tolerance: float = 1e-8,
        training: Mapping[str, Any] | None = None,
    ) -> "ML3MetaModel":
        if len(feature_rows) != len(labels) or not feature_rows:
            raise ValueError("ML3 training rows and labels must be nonempty and aligned")
        if l2 < 0.0 or feature_clip <= 0.0:
            raise ValueError("invalid ML3 training hyperparameters")
        y = np.asarray(labels, dtype=float)
        if not np.all(np.isin(y, [0.0, 1.0])):
            raise ValueError("ML3 labels must be binary")
        if np.unique(y).size != 2:
            raise ValueError("ML3 training requires both target-first and stop-first examples")

        numeric_centers: dict[str, float] = {}
        numeric_scales: dict[str, float] = {}
        for name in NUMERIC_FEATURES:
            column = np.asarray(
                [_finite_float(row.get(name), name) for row in feature_rows],
                dtype=float,
            )
            center, scale = _robust_location_scale(column)
            numeric_centers[name] = center
            numeric_scales[name] = scale

        category_levels: dict[str, tuple[str, ...]] = {}
        for name in CATEGORICAL_FEATURES:
            observed = sorted({_text(row.get(name), name) for row in feature_rows})
            observed = [value for value in observed if value != OTHER_CATEGORY]
            category_levels[name] = tuple(observed + [OTHER_CATEGORY])

        coefficient_names = list(NUMERIC_FEATURES)
        for name in CATEGORICAL_FEATURES:
            coefficient_names.extend(
                f"category:{name}={level}"
                for level in category_levels[name][:-1]
            )
        zero = tuple(0.0 for _ in coefficient_names)
        provisional = cls(
            model_format_version=MODEL_FORMAT_VERSION,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            numeric_features=tuple(NUMERIC_FEATURES),
            categorical_features=tuple(CATEGORICAL_FEATURES),
            category_levels=category_levels,
            numeric_centers=numeric_centers,
            numeric_scales=numeric_scales,
            coefficient_names=tuple(coefficient_names),
            coefficients=zero,
            intercept=0.0,
            feature_clip=float(feature_clip),
            l2=float(l2),
            training={},
        )
        x = np.vstack([provisional.design_vector(row) for row in feature_rows])
        offset = np.asarray(
            [
                _logit(
                    geometry_prior(
                        _finite_float(row.get("gross_rr"), "gross_rr")
                    )
                )
                for row in feature_rows
            ],
            dtype=float,
        )
        n, p = x.shape
        beta = np.zeros(p, dtype=float)
        intercept = 0.0

        def objective(candidate_intercept: float, candidate_beta: np.ndarray) -> float:
            eta = offset + candidate_intercept + x @ candidate_beta
            data_loss = float(np.mean(np.logaddexp(0.0, eta) - y * eta))
            penalty = 0.5 * float(l2) * float(candidate_beta @ candidate_beta)
            return data_loss + penalty

        converged = False
        iterations = 0
        current_loss = objective(intercept, beta)
        for iterations in range(1, maximum_iterations + 1):
            eta = offset + intercept + x @ beta
            probability = np.asarray(_sigmoid(eta), dtype=float)
            residual = probability - y
            weights = np.clip(probability * (1.0 - probability), 1e-8, None)

            gradient = np.empty(p + 1, dtype=float)
            gradient[0] = float(np.mean(residual))
            gradient[1:] = (x.T @ residual) / n + float(l2) * beta

            hessian = np.empty((p + 1, p + 1), dtype=float)
            hessian[0, 0] = float(np.mean(weights)) + 1e-10
            cross = (x.T @ weights) / n
            hessian[0, 1:] = cross
            hessian[1:, 0] = cross
            hessian[1:, 1:] = (x.T * weights) @ x / n
            hessian[1:, 1:] += float(l2) * np.eye(p)
            hessian += 1e-10 * np.eye(p + 1)

            try:
                delta = np.linalg.solve(hessian, gradient)
            except np.linalg.LinAlgError:
                delta = np.linalg.lstsq(hessian, gradient, rcond=None)[0]

            step = 1.0
            accepted = False
            while step >= 1e-6:
                candidate_intercept = intercept - step * delta[0]
                candidate_beta = beta - step * delta[1:]
                candidate_loss = objective(candidate_intercept, candidate_beta)
                if math.isfinite(candidate_loss) and candidate_loss <= current_loss + 1e-12:
                    intercept = float(candidate_intercept)
                    beta = candidate_beta
                    current_loss = candidate_loss
                    accepted = True
                    break
                step *= 0.5
            if not accepted:
                break
            if float(np.max(np.abs(step * delta))) < tolerance:
                converged = True
                break

        fitted_training = dict(training or {})
        fitted_training.update(
            {
                "samples": int(n),
                "positive_rate": float(np.mean(y)),
                "iterations": int(iterations),
                "converged": bool(converged),
                "penalized_log_loss": float(current_loss),
            }
        )
        return cls(
            model_format_version=MODEL_FORMAT_VERSION,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            numeric_features=tuple(NUMERIC_FEATURES),
            categorical_features=tuple(CATEGORICAL_FEATURES),
            category_levels=category_levels,
            numeric_centers=numeric_centers,
            numeric_scales=numeric_scales,
            coefficient_names=tuple(coefficient_names),
            coefficients=tuple(float(value) for value in beta),
            intercept=float(intercept),
            feature_clip=float(feature_clip),
            l2=float(l2),
            training=fitted_training,
        )


__all__ = [
    "MODEL_FORMAT_VERSION",
    "ML3MetaModel",
    "geometry_prior",
]
