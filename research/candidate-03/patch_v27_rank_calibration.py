#!/usr/bin/env python3
"""Replace absolute V27 probability cutoffs with causal rank calibration.

Class-balanced logistic scores preserve ranking but are not calibrated posterior
probabilities. This ablation removes only the absolute probability-scale
assumption. It selects among predeclared top-score fractions on the historical
calibration tail and retains the same Wilson reliability floor and minimum
sample count. Evaluation-week outcomes remain unused.
"""
from __future__ import annotations

import argparse
from pathlib import Path

CONSTANT_OLD = "THRESHOLD_GRID = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85)"
CONSTANT_NEW = "SELECTION_FRACTIONS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)"

FUNCTION = '''def train_model(x: np.ndarray, y: np.ndarray) -> Model:
    if len(y) < 60 or len(np.unique(y)) < 2:
        raise ValueError("insufficient diverse training labels")
    split = max(40, int(len(y) * (1.0 - CALIBRATION_FRACTION)))
    split = min(split, len(y) - 20)
    mean, scale, weights = fit_logistic(x[:split], y[:split])
    calibration_probability = predict_matrix(x[split:], mean, scale, weights)
    calibration_y = y[split:]
    chosen = 1.1
    chosen_count = 0
    chosen_wins = 0
    chosen_lower = 0.0
    for fraction in SELECTION_FRACTIONS:
        cutoff = float(np.quantile(calibration_probability, 1.0 - fraction))
        selected = calibration_probability >= cutoff
        count = int(selected.sum())
        if count < MIN_CALIBRATION_SIGNALS:
            continue
        wins = int(calibration_y[selected].sum())
        lower = wilson_lower(wins, count)
        if lower >= RELIABILITY_FLOOR:
            chosen = cutoff
            chosen_count = count
            chosen_wins = wins
            chosen_lower = lower
            break
    return Model(
        mean=mean,
        scale=scale,
        weights=weights,
        threshold=chosen,
        calibration_count=chosen_count,
        calibration_wins=chosen_wins,
        calibration_lower_bound=chosen_lower,
    )


'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False
    if CONSTANT_NEW not in source:
        if CONSTANT_OLD not in source:
            raise RuntimeError("V27 absolute threshold constant not found")
        source = source.replace(CONSTANT_OLD, CONSTANT_NEW, 1)
        changed = True
    start = source.index("def train_model(")
    end = source.index("def derive_signals(", start)
    if source[start:end] != FUNCTION:
        source = source[:start] + FUNCTION + source[end:]
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
        default=Path("research/candidate-03/derive_nt_lvcfr_v27_signals.py"),
    )
    args = parser.parse_args()
    print(f"V27 rank-calibration patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
