#!/usr/bin/env python3
"""Causal distributed-lag cross-market forecast diagnostic for Candidate 15 V12B.

Unlike V12A's extreme-event conditional beta, this implements the broad external
lead-lag idea directly: current 1/3/5/10 minute returns of the other three allowed
markets forecast the focal market's next ten-minute return. Rolling normal
relations are updated only after each ten-minute response has matured.

This is not a backtest and does not construct synthetic NAV. It produces one
cost-clearing, globally owned diagnostic forecast per ten-minute causal episode.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
from math import isfinite, log
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_cross_predictive_spillover as common
from run_diagnostic import _load_candidate15_v11_runner

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CANDIDATE15 = REPO / "research" / "candidate-15"
SYMBOLS = common.SYMBOLS
FEATURE_HORIZONS = (1, 3, 5, 10)
RESPONSE_MINUTES = 10
MODEL_WINDOWS = (720, 1440, 2880)  # causal 12h / 24h / 48h mature observations
URGENT_ROUND_TRIP_COST = 0.0016


@dataclass(frozen=True, slots=True)
class PendingSample:
    receiver: str
    x: tuple[float, ...]
    source_close: float


@dataclass(frozen=True, slots=True)
class Forecast:
    interval: str
    ts_ns: int
    timestamp: str
    receiver: str
    direction: int
    peer_factor_1m: float
    predicted_return_by_window: dict[int, float]
    conservative_predicted_directional_return: float
    predicted_after_16bps: float
    response_10m: float
    directional_response_10m: float
    directional_response_after_16bps: float
    mfe_10m: float
    mae_10m: float
    focal_event_return: float
    focal_signed_flow: float


class RollingNormalRelation:
    def __init__(self, window: int, features: int) -> None:
        self.window = int(window)
        self.features = int(features)
        self.rows: deque[tuple[np.ndarray, float]] = deque()
        self.xtx = np.zeros((features, features), dtype="float64")
        self.xty = np.zeros(features, dtype="float64")

    def add(self, x: np.ndarray, y: float) -> None:
        x = np.asarray(x, dtype="float64")
        y = float(y)
        if x.shape != (self.features,) or not np.all(np.isfinite(x)) or not isfinite(y):
            return
        self.rows.append((x.copy(), y))
        self.xtx += np.outer(x, x)
        self.xty += x * y
        if len(self.rows) > self.window:
            old_x, old_y = self.rows.popleft()
            self.xtx -= np.outer(old_x, old_x)
            self.xty -= old_x * old_y

    @property
    def ready(self) -> bool:
        return len(self.rows) >= self.window

    def predict(self, x: np.ndarray) -> float | None:
        if not self.ready:
            return None
        scale = float(np.trace(self.xtx)) / max(1, self.features)
        ridge = max(scale * 1e-8, 1e-16)  # numerical conditioning only
        try:
            beta = np.linalg.solve(self.xtx + ridge * np.eye(self.features), self.xty)
        except np.linalg.LinAlgError:
            return None
        value = float(np.dot(beta, x))
        return value if isfinite(value) else None


def _load(interval: str, data_dir: Path) -> tuple[pd.DatetimeIndex, dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    protocol = json.loads((CANDIDATE15 / "protocol-v11.json").read_text(encoding="utf-8"))
    selected = protocol["selection"]["intervals"][interval]
    start = date.fromisoformat(selected["start"])
    end_exclusive = date.fromisoformat(selected["end_exclusive"])
    warmup_start = start - timedelta(days=max(3, int(protocol["selection"]["warmup_days"])))
    runner = _load_candidate15_v11_runner()
    frames: dict[str, pd.DataFrame] = {}
    manifests: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame, manifest = runner.load_symbol_bars(
            symbol,
            warmup_start,
            end_exclusive - timedelta(days=1),
            data_dir,
        )
        frames[symbol] = frame
        manifests.extend(manifest)
    index, arrays = common._aligned_arrays(frames)
    metadata = {
        "interval": interval,
        "start": start.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "manifest_files": len(manifests),
        "manifest_sha256": sorted(item["sha256"] for item in manifests),
    }
    return index, arrays, metadata


def _features(arrays: dict[str, dict[str, np.ndarray]], receiver: str, i: int) -> np.ndarray | None:
    if i < max(FEATURE_HORIZONS):
        return None
    values: list[float] = []
    for peer in SYMBOLS:
        if peer == receiver:
            continue
        close = arrays[peer]["close"]
        for horizon in FEATURE_HORIZONS:
            previous = float(close[i - horizon])
            current = float(close[i])
            if previous <= 0.0 or current <= 0.0:
                return None
            values.append(log(current / previous))
    x = np.asarray(values, dtype="float64")
    if x.shape != (12,) or not np.all(np.isfinite(x)):
        return None
    return x


def _summarize(rows: list[Forecast]) -> dict[str, Any]:
    if not rows:
        return {
            "forecasts": 0,
            "directional_hit_rate": 0.0,
            "mean_directional_response_10m_bps": 0.0,
            "mean_after_16bps_bps": 0.0,
            "median_directional_response_10m_bps": 0.0,
            "median_mfe_10m_bps": 0.0,
            "median_mae_10m_bps": 0.0,
        }
    directional = np.asarray([row.directional_response_10m for row in rows], dtype="float64")
    after = np.asarray([row.directional_response_after_16bps for row in rows], dtype="float64")
    mfe = np.asarray([row.mfe_10m for row in rows], dtype="float64")
    mae = np.asarray([row.mae_10m for row in rows], dtype="float64")
    return {
        "forecasts": len(rows),
        "directional_hit_rate": float(np.mean(directional > 0.0)),
        "mean_directional_response_10m_bps": float(np.mean(directional) * 10000.0),
        "mean_after_16bps_bps": float(np.mean(after) * 10000.0),
        "median_directional_response_10m_bps": float(np.median(directional) * 10000.0),
        "median_mfe_10m_bps": float(np.median(mfe) * 10000.0),
        "median_mae_10m_bps": float(np.median(mae) * 10000.0),
    }


def diagnose(interval: str, output: Path) -> dict[str, Any]:
    index, arrays, metadata = _load(interval, output / "data")
    evaluation_start = pd.Timestamp(metadata["start"], tz="UTC")
    evaluation_end = pd.Timestamp(metadata["end_exclusive"], tz="UTC")
    feature_count = 12
    models = {
        receiver: {
            window: RollingNormalRelation(window, feature_count)
            for window in MODEL_WINDOWS
        }
        for receiver in SYMBOLS
    }
    pending: dict[int, list[PendingSample]] = defaultdict(list)
    forecasts: list[Forecast] = []
    candidate_minutes = 0
    model_ready_minutes = 0
    global_cooldown_until = -1

    for i, timestamp in enumerate(index):
        for sample in pending.pop(i, []):
            current_close = float(arrays[sample.receiver]["close"][i])
            if current_close <= 0.0 or sample.source_close <= 0.0:
                continue
            y = log(current_close / sample.source_close)
            for model in models[sample.receiver].values():
                model.add(np.asarray(sample.x, dtype="float64"), y)

        candidates: list[tuple[float, str, int, dict[int, float], np.ndarray]] = []
        for receiver in SYMBOLS:
            x = _features(arrays, receiver, i)
            if x is None:
                continue
            close = float(arrays[receiver]["close"][i])
            if i + RESPONSE_MINUTES < len(index) and close > 0.0:
                pending[i + RESPONSE_MINUTES].append(
                    PendingSample(receiver=receiver, x=tuple(float(v) for v in x), source_close=close),
                )
            predictions: dict[int, float] = {}
            for window, model in models[receiver].items():
                value = model.predict(x)
                if value is None:
                    predictions = {}
                    break
                predictions[window] = value
            if not predictions:
                continue
            model_ready_minutes += 1
            signs = {1 if value > 0.0 else -1 if value < 0.0 else 0 for value in predictions.values()}
            if 0 in signs or len(signs) != 1:
                continue
            direction = next(iter(signs))
            directional_predictions = [direction * value for value in predictions.values()]
            conservative = min(directional_predictions)
            if conservative <= URGENT_ROUND_TRIP_COST:
                continue
            candidate_minutes += 1
            candidates.append(
                (conservative - URGENT_ROUND_TRIP_COST, receiver, direction, predictions, x),
            )

        if i >= global_cooldown_until and candidates and evaluation_start <= timestamp < evaluation_end:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            _, receiver, direction, predictions, _ = candidates[0]
            geometry = common._future_geometry(arrays, receiver, i, direction)
            if geometry is not None:
                response, mfe, mae = geometry
                peer_returns = [
                    float(arrays[peer]["return"][i])
                    for peer in SYMBOLS
                    if peer != receiver
                ]
                directional_response = direction * response
                conservative = min(direction * value for value in predictions.values())
                forecasts.append(
                    Forecast(
                        interval=interval,
                        ts_ns=int(timestamp.value),
                        timestamp=timestamp.isoformat(),
                        receiver=receiver,
                        direction=direction,
                        peer_factor_1m=float(np.median(peer_returns)),
                        predicted_return_by_window=dict(predictions),
                        conservative_predicted_directional_return=conservative,
                        predicted_after_16bps=conservative - URGENT_ROUND_TRIP_COST,
                        response_10m=response,
                        directional_response_10m=directional_response,
                        directional_response_after_16bps=directional_response - URGENT_ROUND_TRIP_COST,
                        mfe_10m=mfe,
                        mae_10m=mae,
                        focal_event_return=float(arrays[receiver]["return"][i]),
                        focal_signed_flow=float(arrays[receiver]["signed_flow"][i]),
                    ),
                )
                global_cooldown_until = i + RESPONSE_MINUTES

    rows = [asdict(row) for row in forecasts]
    for row in rows:
        row["predicted_return_by_window"] = {
            str(key): value for key, value in row["predicted_return_by_window"].items()
        }
    output.mkdir(parents=True, exist_ok=True)
    (output / "forecasts.json").write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    result = {
        "schema": "candidate-15-v12b-causal-distributed-lag-diagnostic-v1",
        "diagnostic_only_not_account_backtest": True,
        "interval": interval,
        "metadata": metadata,
        "forecast_rows": len(rows),
        "overall": _summarize(forecasts),
        "by_symbol": {
            symbol: _summarize([row for row in forecasts if row.receiver == symbol])
            for symbol in SYMBOLS
        },
        "model_ready_receiver_minutes": model_ready_minutes,
        "cost_clearing_candidate_receiver_minutes": candidate_minutes,
        "model_contract": {
            "features": "OTHER_THREE_MARKETS_LOG_RETURNS_OVER_1_3_5_10_MINUTES",
            "target": "FOCAL_NEXT_10_MINUTE_LOG_RETURN",
            "maturity": "TARGET_ADDED_ONLY_AT_SOURCE_PLUS_10_MINUTES",
            "rolling_windows": list(MODEL_WINDOWS),
            "sign_consensus": "ALL_12H_24H_48H_MODELS",
            "candidate_threshold": "CONSERVATIVE_FORECAST_EXCEEDS_FROZEN_16BPS_URGENT_ROUND_TRIP",
            "global_ownership": "ONE_FORECAST_PER_10_MINUTE_CAUSAL_EPISODE",
            "selection": "MAX_CONSERVATIVE_COSTED_FORECAST_ACROSS_FOUR_ALLOWED_MARKETS",
            "ridge": "NUMERICAL_CONDITIONING_ONLY_1E-8_TRACE_SCALE",
        },
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("interval", choices=("E01", "E02", "E03", "E04", "E05", "E06"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    diagnose(args.interval, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
