#!/usr/bin/env python3
"""Adaptive auction-outcome classifier with causal walk-forward training.

V27 replaces hand-written continuation/reversal thresholds with one stable,
interpretable auction grammar. It finds a completed five-minute breach of the
previous one-hour external range, observes one additional completed five-minute
bar, and estimates the cost-adjusted probability that either the continuation
or reversal scenario reaches +2R before its causal invalidation.

The model is fit only on the 120 calendar days preceding the evaluation week.
Inputs are official Binance futures and spot one-minute klines. Features encode
liquidity penetration, reclaim/acceptance, price response, taker-flow agreement,
basis movement, trend and volatility. A chronological calibration tail chooses
the smallest predeclared probability cutoff whose Wilson lower confidence bound
exceeds the fixed reliability floor. No evaluation-week outcome is used.

This module creates signals only. NautilusTrader remains solely responsible for
orders, fills, fees, funding, positions and NAV.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

NS_PER_MINUTE = 60_000_000_000
FEE = 5.0 / 10_000.0
IMPACT = 1.5 / 10_000.0
TARGET_NET_R = 2.0
TRAINING_DAYS = 120
CALIBRATION_FRACTION = 0.20
RELIABILITY_FLOOR = 0.50
MIN_CALIBRATION_SIGNALS = 12
THRESHOLD_GRID = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85)
FEATURE_NAMES = (
    "penetration_atr",
    "event_close_location",
    "event_body_atr",
    "confirmation_progress_atr",
    "confirmation_beyond_level_atr",
    "futures_event_flow",
    "futures_confirmation_flow",
    "spot_event_flow",
    "spot_confirmation_flow",
    "flow_agreement",
    "basis_change_bp",
    "trend_60m_atr",
    "volatility_ratio",
    "range_compression",
    "hour_sin",
    "hour_cos",
)


@dataclass(frozen=True, slots=True)
class Event:
    event_end_ms: int
    confirmation_end_ms: int
    side: int
    level: float
    event_high: float
    event_low: float
    confirmation_high: float
    confirmation_low: float
    atr: float
    features: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Model:
    mean: np.ndarray
    scale: np.ndarray
    weights: np.ndarray
    threshold: float
    calibration_count: int
    calibration_wins: int
    calibration_lower_bound: float

    def predict(self, values: tuple[float, ...]) -> float:
        x = (np.asarray(values, dtype=float) - self.mean) / self.scale
        z = float(self.weights[0] + np.dot(self.weights[1:], x))
        z = max(-40.0, min(40.0, z))
        return 1.0 / (1.0 + math.exp(-z))


def month_starts(start: date, end: date) -> list[date]:
    current = date(start.year, start.month, 1)
    final = date(end.year, end.month, 1)
    output: list[date] = []
    while current <= final:
        output.append(current)
        current = date(
            current.year + (current.month == 12),
            1 if current.month == 12 else current.month + 1,
            1,
        )
    return output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 0:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "candidate-03-v27"})
    with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as stream:
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)
    temporary.replace(target)


def download_monthly_klines(*, market: str, month: date, root: Path) -> Path:
    stamp = month.strftime("%Y-%m")
    if market == "futures":
        prefix = "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m"
    elif market == "spot":
        prefix = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m"
    else:
        raise ValueError(market)
    name = f"BTCUSDT-1m-{stamp}.zip"
    target = root / market / name
    fetch(f"{prefix}/{name}", target)
    return target


def read_kline_archive(path: Path, market: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}: {members}")
        with archive.open(members[0]) as stream:
            raw = pd.read_csv(stream, header=None, low_memory=False)
    numeric_time = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    raw = raw.loc[numeric_time.notna()].copy()
    raw.iloc[:, 0] = numeric_time.loc[numeric_time.notna()].astype("int64")
    for column in (1, 2, 3, 4, 5, 7, 9, 10):
        raw.iloc[:, column] = pd.to_numeric(raw.iloc[:, column], errors="raise")
    timestamps = raw.iloc[:, 0].astype("int64").to_numpy()
    timestamps = np.where(
        timestamps >= 100_000_000_000_000,
        timestamps // 1_000,
        timestamps,
    )
    return pd.DataFrame(
        {
            "open_time_ms": timestamps.astype("int64"),
            f"{market}_open": raw.iloc[:, 1].astype(float),
            f"{market}_high": raw.iloc[:, 2].astype(float),
            f"{market}_low": raw.iloc[:, 3].astype(float),
            f"{market}_close": raw.iloc[:, 4].astype(float),
            f"{market}_quote": raw.iloc[:, 7].astype(float),
            f"{market}_taker_buy_quote": raw.iloc[:, 10].astype(float),
        }
    )


def load_minutes(
    *, start: datetime, end: datetime, root: Path
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frames: dict[str, list[pd.DataFrame]] = {"futures": [], "spot": []}
    sources: list[dict[str, Any]] = []
    for month in month_starts(start.date(), end.date()):
        for market in ("futures", "spot"):
            path = download_monthly_klines(market=market, month=month, root=root)
            frames[market].append(read_kline_archive(path, market))
            sources.append(
                {
                    "market": market,
                    "month": month.strftime("%Y-%m"),
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    futures = pd.concat(frames["futures"], ignore_index=True).drop_duplicates("open_time_ms")
    spot = pd.concat(frames["spot"], ignore_index=True).drop_duplicates("open_time_ms")
    merged = futures.merge(spot, on="open_time_ms", how="inner").sort_values("open_time_ms")
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    merged = merged[
        (merged.open_time_ms >= start_ms) & (merged.open_time_ms < end_ms)
    ].copy()
    if merged.empty:
        raise ValueError("no aligned futures/spot minute data")
    expected = np.arange(
        merged.open_time_ms.iloc[0],
        merged.open_time_ms.iloc[-1] + 60_000,
        60_000,
    )
    actual = merged.open_time_ms.to_numpy(dtype=np.int64)
    missing = int(len(expected) - len(actual))
    if missing != 0 or not np.array_equal(expected, actual):
        raise ValueError(f"minute data has {missing} missing or duplicate rows")
    return merged.reset_index(drop=True), {"sources": sources, "rows": len(merged)}


def aggregate_five(minutes: pd.DataFrame) -> pd.DataFrame:
    frame = minutes.copy()
    frame["bucket"] = frame.open_time_ms // 300_000
    aggregations: dict[str, Any] = {
        "open_time_ms": "first",
        "futures_open": "first",
        "futures_high": "max",
        "futures_low": "min",
        "futures_close": "last",
        "futures_quote": "sum",
        "futures_taker_buy_quote": "sum",
        "spot_open": "first",
        "spot_high": "max",
        "spot_low": "min",
        "spot_close": "last",
        "spot_quote": "sum",
        "spot_taker_buy_quote": "sum",
    }
    bars = frame.groupby("bucket", sort=True).agg(aggregations).reset_index(drop=True)
    counts = frame.groupby("bucket", sort=True).size().to_numpy()
    bars = bars[counts == 5].copy().reset_index(drop=True)
    bars["end_time_ms"] = bars.open_time_ms + 300_000
    bars["futures_flow"] = (
        2.0 * bars.futures_taker_buy_quote - bars.futures_quote
    ) / bars.futures_quote.replace(0.0, np.nan)
    bars["spot_flow"] = (
        2.0 * bars.spot_taker_buy_quote - bars.spot_quote
    ) / bars.spot_quote.replace(0.0, np.nan)
    bars["basis_bp"] = (bars.futures_close / bars.spot_close - 1.0) * 10_000.0
    previous = bars.futures_close.shift(1)
    true_range = pd.concat(
        [
            bars.futures_high - bars.futures_low,
            (bars.futures_high - previous).abs(),
            (bars.futures_low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr"] = true_range.rolling(12, min_periods=12).mean()
    bars["short_vol"] = bars.futures_close.pct_change().rolling(6, min_periods=6).std()
    bars["long_vol"] = bars.futures_close.pct_change().rolling(36, min_periods=24).std()
    bars["prior_high"] = bars.futures_high.shift(1).rolling(12, min_periods=12).max()
    bars["prior_low"] = bars.futures_low.shift(1).rolling(12, min_periods=12).min()
    bars["range_60"] = bars.prior_high - bars.prior_low
    bars["range_240"] = (
        bars.futures_high.shift(1).rolling(48, min_periods=36).max()
        - bars.futures_low.shift(1).rolling(48, min_periods=36).min()
    )
    bars["trend_60"] = bars.futures_close.shift(1) - bars.futures_close.shift(13)
    return bars


def make_events(bars: pd.DataFrame, *, start_ms: int, end_ms: int) -> list[Event]:
    output: list[Event] = []
    last_event_index = -10
    for index in range(48, len(bars) - 1):
        event = bars.iloc[index]
        confirmation = bars.iloc[index + 1]
        event_end = int(event.end_time_ms)
        if not start_ms <= event_end < end_ms:
            continue
        if index - last_event_index < 3:
            continue
        values = [
            event.atr,
            event.prior_high,
            event.prior_low,
            event.range_60,
            event.range_240,
            event.short_vol,
            event.long_vol,
            event.futures_flow,
            event.spot_flow,
            confirmation.futures_flow,
            confirmation.spot_flow,
        ]
        if not all(math.isfinite(float(value)) for value in values):
            continue
        high_breach = event.futures_high > event.prior_high
        low_breach = event.futures_low < event.prior_low
        if high_breach == low_breach:
            continue
        side = 1 if high_breach else -1
        level = float(event.prior_high if side > 0 else event.prior_low)
        penetration = side * (
            (event.futures_high if side > 0 else event.futures_low) - level
        )
        event_range = max(float(event.futures_high - event.futures_low), 1e-12)
        close_location = (
            (event.futures_close - event.futures_low) / event_range
            if side > 0
            else (event.futures_high - event.futures_close) / event_range
        )
        hour = datetime.fromtimestamp(event_end / 1000, tz=timezone.utc).hour
        feature_values = (
            penetration / event.atr,
            close_location,
            side * (event.futures_close - event.futures_open) / event.atr,
            side * (confirmation.futures_close - event.futures_close) / event.atr,
            side * (confirmation.futures_close - level) / event.atr,
            side * event.futures_flow,
            side * confirmation.futures_flow,
            side * event.spot_flow,
            side * confirmation.spot_flow,
            (side * confirmation.futures_flow) * (side * confirmation.spot_flow),
            side * (confirmation.basis_bp - event.basis_bp),
            side * event.trend_60 / event.atr,
            event.short_vol / max(event.long_vol, 1e-12),
            event.range_60 / max(event.range_240, 1e-12),
            math.sin(2.0 * math.pi * hour / 24.0),
            math.cos(2.0 * math.pi * hour / 24.0),
        )
        if not all(math.isfinite(float(value)) for value in feature_values):
            continue
        output.append(
            Event(
                event_end_ms=event_end,
                confirmation_end_ms=int(confirmation.end_time_ms),
                side=side,
                level=level,
                event_high=float(event.futures_high),
                event_low=float(event.futures_low),
                confirmation_high=float(confirmation.futures_high),
                confirmation_low=float(confirmation.futures_low),
                atr=float(event.atr),
                features=tuple(float(value) for value in feature_values),
            )
        )
        last_event_index = index
    return output


def scenario_stop(event: Event, direction: int) -> float:
    if direction == -event.side:
        return (
            event.event_high + 0.20 * event.atr
            if direction < 0
            else event.event_low - 0.20 * event.atr
        )
    return (
        min(event.level, event.confirmation_low) - 0.20 * event.atr
        if direction > 0
        else max(event.level, event.confirmation_high) + 0.20 * event.atr
    )


def target_trigger(entry: float, stop: float, direction: int) -> float:
    entry_fill = entry * (1.0 + direction * IMPACT)
    stop_fill = stop * (1.0 - direction * IMPACT)
    loss = abs(entry_fill - stop_fill) + entry_fill * FEE + stop_fill * FEE
    reward = TARGET_NET_R * loss
    if direction > 0:
        return (reward + entry_fill * (1.0 + FEE)) / (
            (1.0 - IMPACT) * (1.0 - FEE)
        )
    return (entry_fill * (1.0 - FEE) - reward) / (
        (1.0 + IMPACT) * (1.0 + FEE)
    )


def label_scenario(minutes: pd.DataFrame, event: Event, direction: int) -> int:
    entry_ms = event.confirmation_end_ms
    rows = minutes[
        (minutes.open_time_ms >= entry_ms)
        & (minutes.open_time_ms < entry_ms + 180 * 60_000)
    ]
    if rows.empty:
        return 0
    entry = float(rows.iloc[0].futures_open)
    stop = scenario_stop(event, direction)
    if direction * (entry - stop) <= 0.0:
        return 0
    target = target_trigger(entry, stop, direction)
    for row in rows.itertuples(index=False):
        stop_hit = row.futures_low <= stop if direction > 0 else row.futures_high >= stop
        target_hit = row.futures_high >= target if direction > 0 else row.futures_low <= target
        if stop_hit:
            return 0
        if target_hit:
            return 1
    return 0


def fit_logistic(
    x: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    normalized = (x - mean) / scale
    design = np.column_stack([np.ones(len(normalized)), normalized])
    weights = np.zeros(design.shape[1], dtype=float)
    positive = max(float(y.sum()), 1.0)
    negative = max(float(len(y) - y.sum()), 1.0)
    sample_weight = np.where(
        y > 0.5,
        len(y) / (2.0 * positive),
        len(y) / (2.0 * negative),
    )
    for iteration in range(800):
        logits = np.clip(design @ weights, -30.0, 30.0)
        probability = 1.0 / (1.0 + np.exp(-logits))
        gradient = design.T @ ((probability - y) * sample_weight) / len(y)
        gradient[1:] += 0.03 * weights[1:]
        learning_rate = 0.12 / math.sqrt(1.0 + iteration / 80.0)
        weights -= learning_rate * gradient
    return mean, scale, weights


def predict_matrix(
    x: np.ndarray, mean: np.ndarray, scale: np.ndarray, weights: np.ndarray
) -> np.ndarray:
    normalized = (x - mean) / scale
    logits = np.clip(weights[0] + normalized @ weights[1:], -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-logits))


def wilson_lower(
    wins: int, count: int, z: float = 1.2815515655446004
) -> float:
    if count <= 0:
        return 0.0
    p = wins / count
    denominator = 1.0 + z * z / count
    center = p + z * z / (2.0 * count)
    margin = z * math.sqrt(
        p * (1.0 - p) / count + z * z / (4.0 * count * count)
    )
    return (center - margin) / denominator


def train_model(x: np.ndarray, y: np.ndarray) -> Model:
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
    for threshold in THRESHOLD_GRID:
        selected = calibration_probability >= threshold
        count = int(selected.sum())
        if count < MIN_CALIBRATION_SIGNALS:
            continue
        wins = int(calibration_y[selected].sum())
        lower = wilson_lower(wins, count)
        if lower >= RELIABILITY_FLOOR:
            chosen = threshold
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


def derive_signals(
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
        root=prepared_root / "adaptive_training",
    )
    bars = aggregate_five(minutes)
    training_events = make_events(
        bars,
        start_ms=int(training_start.timestamp() * 1000),
        end_ms=int((evaluation_start - timedelta(minutes=180)).timestamp() * 1000),
    )
    evaluation_events = make_events(
        bars,
        start_ms=int(evaluation_start.timestamp() * 1000),
        end_ms=int(evaluation_end.timestamp() * 1000),
    )
    x = np.asarray([event.features for event in training_events], dtype=float)
    y_reversal = np.asarray(
        [
            label_scenario(minutes, event, -event.side)
            for event in training_events
        ],
        dtype=float,
    )
    y_continuation = np.asarray(
        [
            label_scenario(minutes, event, event.side)
            for event in training_events
        ],
        dtype=float,
    )
    reversal_model = train_model(x, y_reversal)
    continuation_model = train_model(x, y_continuation)

    signals: list[dict[str, Any]] = []
    no_trade: dict[str, int] = {}
    for event in evaluation_events:
        reversal_probability = reversal_model.predict(event.features)
        continuation_probability = continuation_model.predict(event.features)
        reversal_pass = reversal_probability >= reversal_model.threshold
        continuation_pass = continuation_probability >= continuation_model.threshold
        if reversal_pass and continuation_pass:
            if abs(reversal_probability - continuation_probability) < 0.05:
                no_trade["AMBIGUOUS_DIRECTIONAL_PROBABILITIES"] = (
                    no_trade.get("AMBIGUOUS_DIRECTIONAL_PROBABILITIES", 0) + 1
                )
                continue
            direction = (
                -event.side
                if reversal_probability > continuation_probability
                else event.side
            )
            probability = max(reversal_probability, continuation_probability)
            state = (
                "ADAPTIVE_AUCTION_REVERSAL"
                if direction == -event.side
                else "ADAPTIVE_AUCTION_CONTINUATION"
            )
        elif reversal_pass:
            direction = -event.side
            probability = reversal_probability
            state = "ADAPTIVE_AUCTION_REVERSAL"
        elif continuation_pass:
            direction = event.side
            probability = continuation_probability
            state = "ADAPTIVE_AUCTION_CONTINUATION"
        else:
            no_trade["NO_CALIBRATED_DIRECTION"] = (
                no_trade.get("NO_CALIBRATED_DIRECTION", 0) + 1
            )
            continue
        stop = scenario_stop(event, direction)
        reference = (
            event.confirmation_high if direction > 0 else event.confirmation_low
        )
        if direction * (reference - stop) <= 0.0:
            no_trade["INVALID_CAUSAL_STOP"] = (
                no_trade.get("INVALID_CAUSAL_STOP", 0) + 1
            )
            continue
        confirm_ns = event.confirmation_end_ms * 1_000_000
        suffix = hashlib.sha256(
            f"{confirm_ns}|{direction}|{state}|{probability:.12g}".encode()
        ).hexdigest()[:16]
        details = {
            "scenario_kind": state,
            "entry_kind": "CONTINUATION",
            "event_side": event.side,
            "event_level": event.level,
            "event_high": event.event_high,
            "event_low": event.event_low,
            "confirmation_high": event.confirmation_high,
            "confirmation_low": event.confirmation_low,
            "reversal_probability": reversal_probability,
            "continuation_probability": continuation_probability,
            "selected_probability": probability,
            "reversal_threshold": reversal_model.threshold,
            "continuation_threshold": continuation_model.threshold,
            "feature_names": list(FEATURE_NAMES),
            "features": list(event.features),
            "training_window_days": TRAINING_DAYS,
            "target_net_r": TARGET_NET_R,
        }
        signals.append(
            {
                "scenario_id": f"NT-LVCFR-V27-{state}-{suffix}",
                "scenario_kind": state,
                "entry_kind": "CONTINUATION",
                "confirm_time_ns": confirm_ns,
                "eligible_time_ns": confirm_ns,
                "direction": direction,
                "initial_stop": stop,
                "atr": event.atr,
                "first_start_time_ns": (
                    event.event_end_ms * 1_000_000 - 300 * 1_000_000_000
                ),
                "first_end_time_ns": event.event_end_ms * 1_000_000,
                "target_mode": "EXISTING_NET_R_OBJECTIVE",
                "disable_rapid_failure_reversal": True,
                "details": details,
            }
        )

    output = prepared_root / "signals.json"
    output.write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    state_counts = {}
    if signals:
        state_counts = {
            str(key): int(value)
            for key, value in pd.Series(
                [signal["scenario_kind"] for signal in signals]
            ).value_counts().items()
        }
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v27-adaptive-auction-classifier",
        "engine_status": "causal_walk_forward_model_schedule_only_no_backtest",
        "week_start": week_start.isoformat(),
        "training_start": training_start.isoformat(),
        "training_end": evaluation_start.isoformat(),
        "training_events": len(training_events),
        "training_reversal_win_rate": float(y_reversal.mean()),
        "training_continuation_win_rate": float(y_continuation.mean()),
        "evaluation_events": len(evaluation_events),
        "derived_signal_count": len(signals),
        "state_counts": state_counts,
        "no_trade_reasons": dict(sorted(no_trade.items())),
        "reversal_model": {
            "threshold": reversal_model.threshold,
            "calibration_count": reversal_model.calibration_count,
            "calibration_wins": reversal_model.calibration_wins,
            "calibration_lower_bound": reversal_model.calibration_lower_bound,
        },
        "continuation_model": {
            "threshold": continuation_model.threshold,
            "calibration_count": continuation_model.calibration_count,
            "calibration_wins": continuation_model.calibration_wins,
            "calibration_lower_bound": continuation_model.calibration_lower_bound,
        },
        "source_manifest": source_manifest,
        "feature_names": list(FEATURE_NAMES),
        "selection_policy": (
            "pre-evaluation 120-day causal training; chronological calibration; "
            "fixed Wilson reliability floor; no evaluation-week labels or "
            "return-threshold search"
        ),
    }
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return signals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    signals = derive_signals(
        week_start=args.week_start,
        prepared_root=args.prepared_root.resolve(),
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"candidate": "V27", "signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
