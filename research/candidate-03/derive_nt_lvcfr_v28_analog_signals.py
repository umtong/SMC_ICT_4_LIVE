#!/usr/bin/env python3
"""V28 one-variable ablation: local historical analogs replace linear scores.

The structural labels, 120-day causal window, unseen weeks, completed retest
logic, reliability floor and native execution are unchanged. Only the additive
logistic model assumption is removed. For each event, an 80-nearest-neighbor
set in standardized causal feature space estimates continuation and reversal
state frequencies. Chronological calibration selects a fixed top-score rank
whose Wilson lower confidence bound exceeds the same reliability floor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from derive_nt_lvcfr_v27_signals import (
    CALIBRATION_FRACTION,
    FEATURE_NAMES,
    aggregate_five,
    load_minutes,
    make_events,
    wilson_lower,
)
from derive_nt_lvcfr_v28_signals import (
    RETEST_EXPIRY_MINUTES,
    RELIABILITY_FLOOR,
    SELECTION_FRACTIONS,
    STOP_BUFFER_ATR,
    STRUCTURAL_BARRIER_ATR,
    TRAINING_DAYS,
    causal_stop,
    event_index_map,
    find_retest,
    structural_label,
)

K_NEIGHBORS = 80
MIN_CALIBRATION_SIGNALS = 12


@dataclass(frozen=True, slots=True)
class AnalogModel:
    mean: np.ndarray
    scale: np.ndarray
    reference_x: np.ndarray
    reference_y: np.ndarray
    threshold: float
    calibration_count: int
    calibration_wins: int
    calibration_lower_bound: float
    state: str

    def predict(self, values: tuple[float, ...]) -> float:
        query = (np.asarray(values, dtype=float) - self.mean) / self.scale
        distance = np.sum((self.reference_x - query) ** 2, axis=1)
        count = min(K_NEIGHBORS, len(distance))
        nearest = np.argpartition(distance, count - 1)[:count]
        selected_distance = distance[nearest]
        weights = 1.0 / np.maximum(np.sqrt(selected_distance), 1e-6)
        return float(np.dot(weights, self.reference_y[nearest]) / weights.sum())


def standardized(
    reference: np.ndarray, query: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mean = reference.mean(axis=0)
    scale = reference.std(axis=0)
    scale[scale < 1e-9] = 1.0
    return mean, scale, (reference - mean) / scale, (query - mean) / scale


def analog_scores(reference_x: np.ndarray, reference_y: np.ndarray, query_x: np.ndarray) -> np.ndarray:
    mean, scale, normalized_reference, normalized_query = standardized(reference_x, query_x)
    del mean, scale
    output = np.empty(len(normalized_query), dtype=float)
    for index, query in enumerate(normalized_query):
        distance = np.sum((normalized_reference - query) ** 2, axis=1)
        count = min(K_NEIGHBORS, len(distance))
        nearest = np.argpartition(distance, count - 1)[:count]
        selected_distance = distance[nearest]
        weights = 1.0 / np.maximum(np.sqrt(selected_distance), 1e-6)
        output[index] = float(np.dot(weights, reference_y[nearest]) / weights.sum())
    return output


def train_analog(x: np.ndarray, y: np.ndarray, state: str) -> AnalogModel:
    if len(y) < 200 or len(np.unique(y)) < 2:
        raise ValueError(f"insufficient diverse {state} labels")
    split = max(160, int(len(y) * (1.0 - CALIBRATION_FRACTION)))
    split = min(split, len(y) - 80)
    base_x = x[:split]
    base_y = y[:split]
    calibration_x = x[split:]
    calibration_y = y[split:]
    scores = analog_scores(base_x, base_y, calibration_x)
    threshold = 1.1
    selected_count = 0
    selected_wins = 0
    selected_lower = 0.0
    for fraction in SELECTION_FRACTIONS:
        cutoff = float(np.quantile(scores, 1.0 - fraction))
        selected = scores >= cutoff
        count = int(selected.sum())
        if count < MIN_CALIBRATION_SIGNALS:
            continue
        wins = int(calibration_y[selected].sum())
        lower = wilson_lower(wins, count)
        if lower >= RELIABILITY_FLOOR:
            threshold = cutoff
            selected_count = count
            selected_wins = wins
            selected_lower = lower
            break
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    return AnalogModel(
        mean=mean,
        scale=scale,
        reference_x=(x - mean) / scale,
        reference_y=y,
        threshold=threshold,
        calibration_count=selected_count,
        calibration_wins=selected_wins,
        calibration_lower_bound=selected_lower,
        state=state,
    )


def model_manifest(model: AnalogModel) -> dict[str, Any]:
    return {
        "state": model.state,
        "model": "distance_weighted_80_nearest_historical_analogs",
        "threshold": model.threshold,
        "calibration_count": model.calibration_count,
        "calibration_wins": model.calibration_wins,
        "calibration_lower_bound": model.calibration_lower_bound,
    }


def derive_analog(
    *, week_start: date, prepared_root: Path, output_manifest: Path
) -> list[dict[str, Any]]:
    evaluation_start = datetime.combine(
        week_start, datetime.min.time(), tzinfo=timezone.utc
    )
    evaluation_end = evaluation_start + timedelta(days=7)
    training_start = evaluation_start - timedelta(days=TRAINING_DAYS)
    minutes, source_manifest = load_minutes(
        start=training_start - timedelta(days=3),
        end=evaluation_end + timedelta(days=1),
        root=prepared_root / "structural_training",
    )
    bars = aggregate_five(minutes)
    indices = event_index_map(bars)
    training_events = make_events(
        bars,
        start_ms=int(training_start.timestamp() * 1000),
        end_ms=int((evaluation_start - timedelta(minutes=30)).timestamp() * 1000),
    )
    evaluation_events = make_events(
        bars,
        start_ms=int(evaluation_start.timestamp() * 1000),
        end_ms=int(evaluation_end.timestamp() * 1000),
    )
    x = np.asarray([event.features for event in training_events], dtype=float)
    labels = np.asarray(
        [structural_label(bars, indices, event) for event in training_events],
        dtype=int,
    )
    continuation = train_analog(x, (labels == 1).astype(float), "STRUCTURAL_CONTINUATION")
    reversal = train_analog(x, (labels == -1).astype(float), "STRUCTURAL_REVERSAL")

    signals: list[dict[str, Any]] = []
    no_trade: dict[str, int] = {}
    for event in evaluation_events:
        continuation_score = continuation.predict(event.features)
        reversal_score = reversal.predict(event.features)
        continuation_pass = continuation_score >= continuation.threshold
        reversal_pass = reversal_score >= reversal.threshold
        if continuation_pass and reversal_pass:
            if abs(continuation_score - reversal_score) < 0.05:
                no_trade["AMBIGUOUS_ANALOG_STATE"] = no_trade.get("AMBIGUOUS_ANALOG_STATE", 0) + 1
                continue
            direction = event.side if continuation_score > reversal_score else -event.side
        elif continuation_pass:
            direction = event.side
        elif reversal_pass:
            direction = -event.side
        else:
            no_trade["NO_RELIABLE_ANALOG_STATE"] = no_trade.get("NO_RELIABLE_ANALOG_STATE", 0) + 1
            continue
        retest, reason = find_retest(minutes, event, direction)
        if retest is None:
            no_trade[reason] = no_trade.get(reason, 0) + 1
            continue
        stop = causal_stop(event, retest, direction)
        reference = float(retest.futures_close)
        if direction * (reference - stop) <= 0.0:
            no_trade["INVALID_RETEST_STOP"] = no_trade.get("INVALID_RETEST_STOP", 0) + 1
            continue
        confirm_ns = (int(retest.open_time_ms) + 60_000) * 1_000_000
        state = (
            "ANALOG_STRUCTURAL_CONTINUATION_RETEST"
            if direction == event.side
            else "ANALOG_STRUCTURAL_REVERSAL_RETEST"
        )
        suffix = hashlib.sha256(
            f"{confirm_ns}|{direction}|{state}|{event.level:.12g}".encode()
        ).hexdigest()[:16]
        signals.append(
            {
                "scenario_id": f"NT-LVCFR-V28A-{state}-{suffix}",
                "scenario_kind": state,
                "entry_kind": "CONTINUATION",
                "confirm_time_ns": confirm_ns,
                "eligible_time_ns": confirm_ns,
                "direction": direction,
                "initial_stop": stop,
                "atr": event.atr,
                "first_start_time_ns": event.event_end_ms * 1_000_000 - 300_000_000_000,
                "first_end_time_ns": event.event_end_ms * 1_000_000,
                "target_mode": "EXISTING_NET_R_OBJECTIVE",
                "disable_rapid_failure_reversal": True,
                "details": {
                    "scenario_kind": state,
                    "entry_kind": "CONTINUATION",
                    "event_side": event.side,
                    "event_level": event.level,
                    "event_high": event.event_high,
                    "event_low": event.event_low,
                    "retest_reason": reason,
                    "continuation_analog_score": continuation_score,
                    "reversal_analog_score": reversal_score,
                    "continuation_threshold": continuation.threshold,
                    "reversal_threshold": reversal.threshold,
                    "structural_barrier_atr": STRUCTURAL_BARRIER_ATR,
                    "retest_expiry_minutes": RETEST_EXPIRY_MINUTES,
                    "stop_buffer_atr": STOP_BUFFER_ATR,
                    "feature_names": list(FEATURE_NAMES),
                    "features": list(event.features),
                },
            }
        )
    signals.sort(key=lambda item: (int(item["confirm_time_ns"]), str(item["scenario_id"])))
    (prepared_root / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    counts: dict[str, int] = {}
    for signal in signals:
        state = str(signal["scenario_kind"])
        counts[state] = counts.get(state, 0) + 1
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v28-analog-ablation",
        "engine_status": "causal_structural_analog_schedule_only_no_backtest",
        "week_start": week_start.isoformat(),
        "training_start": training_start.isoformat(),
        "training_end": evaluation_start.isoformat(),
        "training_events": len(training_events),
        "training_label_counts": {
            "continuation": int((labels == 1).sum()),
            "reversal": int((labels == -1).sum()),
            "unresolved": int((labels == 0).sum()),
        },
        "evaluation_events": len(evaluation_events),
        "derived_signal_count": len(signals),
        "state_counts": dict(sorted(counts.items())),
        "no_trade_reasons": dict(sorted(no_trade.items())),
        "continuation_model": model_manifest(continuation),
        "reversal_model": model_manifest(reversal),
        "source_manifest": source_manifest,
        "ablation": "removed additive linear score; all state labels, reliability and retest contracts unchanged",
    }
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return signals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    signals = derive_analog(
        week_start=args.week_start,
        prepared_root=args.prepared_root.resolve(),
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"candidate": "V28_ANALOG", "signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
