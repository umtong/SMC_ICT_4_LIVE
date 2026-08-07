#!/usr/bin/env python3
"""Replace V31's unavailable sklearn model with a deterministic NumPy model.

The feature set, cost-aware labels, causal fit/calibration split, rank threshold,
retest requirement, weeks and native execution are unchanged. The replacement
uses fixed random nonlinear features and weighted ridge classification. Rank
calibration means only score ordering, not posterior calibration, is required.
"""
from __future__ import annotations

import argparse
from pathlib import Path

IMPORT_OLD = "from sklearn.ensemble import HistGradientBoostingClassifier\n"
CLASS_MARKER = "@dataclass(frozen=True, slots=True)\nclass DirectionModel:"
CLASS_CODE = '''class RandomFeatureClassifier:
    def __init__(
        self,
        *,
        hidden_features: int = 256,
        l2_regularization: float = 5.0,
        random_state: int = 31,
    ) -> None:
        self.hidden_features = hidden_features
        self.l2_regularization = l2_regularization
        self.random_state = random_state
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None
        self.projection_: np.ndarray | None = None
        self.bias_: np.ndarray | None = None
        self.coef_: np.ndarray | None = None

    def _design(self, x: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("model not fitted")
        if self.projection_ is None or self.bias_ is None:
            raise RuntimeError("random features not fitted")
        normalized = (x - self.mean_) / self.scale_
        hidden = np.tanh(normalized @ self.projection_ + self.bias_)
        return np.column_stack([np.ones(len(x)), normalized, hidden])

    def fit(
        self,
        x: np.ndarray,
        y: np.ndarray,
        sample_weight: np.ndarray | None = None,
    ) -> "RandomFeatureClassifier":
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        self.mean_ = x.mean(axis=0)
        self.scale_ = x.std(axis=0)
        self.scale_[self.scale_ < 1e-9] = 1.0
        rng = np.random.default_rng(self.random_state)
        self.projection_ = rng.normal(
            0.0,
            1.0 / math.sqrt(max(1, x.shape[1])),
            size=(x.shape[1], self.hidden_features),
        )
        self.bias_ = rng.uniform(-math.pi, math.pi, size=self.hidden_features)
        design = self._design(x)
        weights = (
            np.ones(len(y), dtype=float)
            if sample_weight is None
            else np.asarray(sample_weight, dtype=float)
        )
        root_weight = np.sqrt(weights)
        weighted_design = design * root_weight[:, None]
        target = (2.0 * y - 1.0) * root_weight
        penalty = np.eye(design.shape[1], dtype=float) * self.l2_regularization
        penalty[0, 0] = 0.0
        gram = weighted_design.T @ weighted_design + penalty
        rhs = weighted_design.T @ target
        self.coef_ = np.linalg.solve(gram, rhs)
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if self.coef_ is None:
            raise RuntimeError("model not fitted")
        score = np.clip(self._design(np.asarray(x, dtype=float)) @ self.coef_, -20.0, 20.0)
        probability = 1.0 / (1.0 + np.exp(-score))
        return np.column_stack([1.0 - probability, probability])


'''
ESTIMATOR_OLD = "    estimator: HistGradientBoostingClassifier\n"
ESTIMATOR_NEW = "    estimator: RandomFeatureClassifier\n"
FIT_OLD = '''    estimator = HistGradientBoostingClassifier(
        loss="log_loss",
        learning_rate=0.05,
        max_iter=160,
        max_leaf_nodes=15,
        min_samples_leaf=50,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=31,
    )
    estimator.fit(x_fit, y_fit, sample_weight=weights)
'''
FIT_NEW = '''    estimator = RandomFeatureClassifier(
        hidden_features=256,
        l2_regularization=5.0,
        random_state=31,
    )
    estimator.fit(x_fit, y_fit, sample_weight=weights)
'''
MANIFEST_OLD = '        "model": "HistGradientBoostingClassifier",\n'
MANIFEST_NEW = '        "model": "deterministic_numpy_random_feature_ridge",\n'


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False
    if IMPORT_OLD in source:
        source = source.replace(IMPORT_OLD, "", 1)
        changed = True
    if CLASS_CODE not in source:
        if CLASS_MARKER not in source:
            raise RuntimeError("V31 DirectionModel marker not found")
        source = source.replace(CLASS_MARKER, CLASS_CODE + CLASS_MARKER, 1)
        changed = True
    for old, new in (
        (ESTIMATOR_OLD, ESTIMATOR_NEW),
        (FIT_OLD, FIT_NEW),
        (MANIFEST_OLD, MANIFEST_NEW),
    ):
        if new in source:
            continue
        if old not in source:
            raise RuntimeError("V31 sklearn block not found")
        source = source.replace(old, new, 1)
        changed = True
    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path("research/candidate-03/derive_nt_lvcfr_v31_signals.py"),
    )
    args = parser.parse_args()
    print(f"V31 NumPy model patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
