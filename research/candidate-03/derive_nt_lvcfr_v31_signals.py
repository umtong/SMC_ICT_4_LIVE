#!/usr/bin/env python3
"""V31 walk-forward nonlinear auction outcome model.

The prior rule-based families repeatedly failed because a small collection of
additive thresholds could not represent interactions among cross-asset return,
spot/perpetual flow, volatility and local range state. V31 samples every 30
minutes, fits fixed HistGradientBoosting models on the 150-day fit portion of a
strictly pre-evaluation 180-day window, and calibrates model ranks on the final
30 pre-evaluation days.

Labels are cost-aware 1.25R-before-stop outcomes conditional on a completed
one-minute pullback defense. Evaluation-week outcomes are never used. A model
score is not an entry: the same completed pullback defense must occur in the
evaluation week. This module creates signals only; NautilusTrader remains the
sole order, fill, fee, funding, position and NAV engine.
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
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from derive_nt_lvcfr_v27_signals import wilson_lower
from derive_nt_lvcfr_v29_signals import (
    SYMBOLS,
    aggregate_bars,
    load_minutes,
    minute_flow,
)

TRAINING_DAYS = 180
CALIBRATION_DAYS = 30
SAMPLE_EVERY_BARS = 10  # 3-minute bars -> one independent candidate / 30m
RETEST_EXPIRY_MINUTES = 10
OUTCOME_HORIZON_MINUTES = 60
STOP_BUFFER_ATR = 0.10
TARGET_NET_R = 1.25
FEE = 5.0 / 10_000.0
IMPACT = 1.5 / 10_000.0
MIN_CALIBRATION_SIGNALS = 16
RELIABILITY_FLOOR = 0.50
SELECTION_FRACTIONS = (0.025, 0.04, 0.06, 0.08, 0.10, 0.15)
MIN_SIGNAL_SEPARATION_MINUTES = 30
RETURN_WINDOWS = (1, 2, 5, 10, 20)
FLOW_WINDOWS = (1, 3, 10)


@dataclass(frozen=True, slots=True)
class LabeledRetest:
    bar_index: int
    retest_open_time_ms: int
    entry_reference: float
    stop: float
    won: int


@dataclass(frozen=True, slots=True)
class DirectionModel:
    estimator: HistGradientBoostingClassifier
    threshold: float
    calibration_count: int
    calibration_wins: int
    calibration_lower_bound: float
    direction: int

    def score(self, values: np.ndarray) -> float:
        return float(self.estimator.predict_proba(values.reshape(1, -1))[0, 1])


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def feature_frame(bars: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame({"end_time_ms": bars.end_time_ms.astype("int64")})
    for symbol in SYMBOLS:
        lower = symbol.lower()
        for market in ("futures", "spot"):
            prefix = f"{lower}_{market}"
            close = bars[f"{prefix}_close"]
            flow = bars[f"{prefix}_flow"]
            quote = bars[f"{prefix}_quote"]
            for window in RETURN_WINDOWS:
                output[f"{prefix}_return_{window}"] = np.log(
                    close / close.shift(window)
                )
            for window in FLOW_WINDOWS:
                output[f"{prefix}_flow_mean_{window}"] = flow.rolling(
                    window, min_periods=window
                ).mean()
            output[f"{prefix}_flow_std_10"] = flow.rolling(10, min_periods=10).std()
            baseline_quote = quote.shift(1).rolling(20, min_periods=12).median()
            output[f"{prefix}_log_quote_ratio"] = np.log(
                safe_ratio(quote, baseline_quote)
            )
        output[f"{lower}_basis_bp"] = (
            bars[f"{lower}_futures_close"] / bars[f"{lower}_spot_close"] - 1.0
        ) * 10_000.0
        output[f"{lower}_basis_change_5"] = (
            output[f"{lower}_basis_bp"] - output[f"{lower}_basis_bp"].shift(5)
        )

    leader_returns = pd.concat(
        [
            output[f"{symbol.lower()}_futures_return_1"]
            for symbol in SYMBOLS[1:]
        ],
        axis=1,
    )
    output["leader_common_return"] = leader_returns.median(axis=1)
    output["leader_return_dispersion"] = leader_returns.std(axis=1)
    output["btc_common_residual"] = (
        output["btcusdt_futures_return_1"] - output["leader_common_return"]
    )
    output["btc_futures_spot_return_gap"] = (
        output["btcusdt_futures_return_1"] - output["btcusdt_spot_return_1"]
    )
    output["btc_futures_spot_flow_gap"] = (
        output["btcusdt_futures_flow_mean_1"]
        - output["btcusdt_spot_flow_mean_1"]
    )
    output["btc_atr_fraction"] = safe_ratio(
        bars.btc_atr, bars.btcusdt_futures_close
    )
    output["short_long_vol_ratio"] = safe_ratio(
        bars.short_vol, bars.long_vol
    )
    output["causal_beta"] = bars.beta
    output["btc_range_position_20"] = safe_ratio(
        bars.btcusdt_futures_close
        - bars.btcusdt_futures_low.shift(1).rolling(20, min_periods=20).min(),
        bars.btcusdt_futures_high.shift(1).rolling(20, min_periods=20).max()
        - bars.btcusdt_futures_low.shift(1).rolling(20, min_periods=20).min(),
    )
    output["btc_range_position_80"] = safe_ratio(
        bars.btcusdt_futures_close
        - bars.btcusdt_futures_low.shift(1).rolling(80, min_periods=60).min(),
        bars.btcusdt_futures_high.shift(1).rolling(80, min_periods=60).max()
        - bars.btcusdt_futures_low.shift(1).rolling(80, min_periods=60).min(),
    )
    timestamp = pd.to_datetime(output.end_time_ms, unit="ms", utc=True)
    hour = timestamp.dt.hour + timestamp.dt.minute / 60.0
    weekday = timestamp.dt.dayofweek
    output["hour_sin"] = np.sin(2.0 * math.pi * hour / 24.0)
    output["hour_cos"] = np.cos(2.0 * math.pi * hour / 24.0)
    output["weekday_sin"] = np.sin(2.0 * math.pi * weekday / 7.0)
    output["weekday_cos"] = np.cos(2.0 * math.pi * weekday / 7.0)
    return output


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


def find_retest(
    minutes: pd.DataFrame,
    bars: pd.DataFrame,
    bar_index: int,
    direction: int,
) -> tuple[Any | None, float | None]:
    event = bars.iloc[bar_index]
    start_ms = int(event.end_time_ms)
    future = minutes[
        (minutes.open_time_ms >= start_ms)
        & (minutes.open_time_ms < start_ms + RETEST_EXPIRY_MINUTES * 60_000)
    ]
    zone_mid = (
        float(event.btcusdt_futures_open) + float(event.btcusdt_futures_close)
    ) / 2.0
    for row in future.itertuples(index=False):
        close = float(row.btcusdt_futures_close)
        open_ = float(row.btcusdt_futures_open)
        if direction > 0:
            touched = float(row.btcusdt_futures_low) <= zone_mid
            defended = close > zone_mid and close > open_
        else:
            touched = float(row.btcusdt_futures_high) >= zone_mid
            defended = close < zone_mid and close < open_
        if not touched or not defended:
            continue
        if (
            direction * minute_flow(row, "BTCUSDT", "futures") <= 0.0
            or direction * minute_flow(row, "BTCUSDT", "spot") <= 0.0
        ):
            continue
        return row, zone_mid
    return None, None


def label_retest(
    minutes: pd.DataFrame,
    bars: pd.DataFrame,
    bar_index: int,
    direction: int,
) -> LabeledRetest | None:
    event = bars.iloc[bar_index]
    at = float(event.btc_atr)
    if not math.isfinite(at) or at <= 0.0:
        return None
    retest, _ = find_retest(minutes, bars, bar_index, direction)
    if retest is None:
        return None
    entry = float(retest.btcusdt_futures_close)
    stop = (
        min(float(event.btcusdt_futures_low), float(retest.btcusdt_futures_low))
        - STOP_BUFFER_ATR * at
        if direction > 0
        else max(float(event.btcusdt_futures_high), float(retest.btcusdt_futures_high))
        + STOP_BUFFER_ATR * at
    )
    if direction * (entry - stop) <= 0.0:
        return None
    target = target_trigger(entry, stop, direction)
    entry_ms = int(retest.open_time_ms) + 60_000
    future = minutes[
        (minutes.open_time_ms >= entry_ms)
        & (minutes.open_time_ms < entry_ms + OUTCOME_HORIZON_MINUTES * 60_000)
    ]
    won = 0
    for row in future.itertuples(index=False):
        stop_hit = (
            float(row.btcusdt_futures_low) <= stop
            if direction > 0
            else float(row.btcusdt_futures_high) >= stop
        )
        target_hit = (
            float(row.btcusdt_futures_high) >= target
            if direction > 0
            else float(row.btcusdt_futures_low) <= target
        )
        if stop_hit:
            won = 0
            break
        if target_hit:
            won = 1
            break
    return LabeledRetest(
        bar_index=bar_index,
        retest_open_time_ms=int(retest.open_time_ms),
        entry_reference=entry,
        stop=stop,
        won=won,
    )


def calibrate_threshold(scores: np.ndarray, labels: np.ndarray) -> tuple[float, int, int, float]:
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
        wins = int(labels[selected].sum())
        lower = wilson_lower(wins, count)
        if lower >= RELIABILITY_FLOOR:
            threshold = cutoff
            selected_count = count
            selected_wins = wins
            selected_lower = lower
            break
    return threshold, selected_count, selected_wins, selected_lower


def train_direction(
    x_fit: np.ndarray,
    y_fit: np.ndarray,
    x_calibration: np.ndarray,
    y_calibration: np.ndarray,
    direction: int,
) -> DirectionModel:
    if len(y_fit) < 300 or len(np.unique(y_fit)) < 2:
        raise ValueError(f"insufficient direction={direction} fit labels")
    positive = max(float(y_fit.sum()), 1.0)
    negative = max(float(len(y_fit) - y_fit.sum()), 1.0)
    weights = np.where(
        y_fit > 0.5,
        len(y_fit) / (2.0 * positive),
        len(y_fit) / (2.0 * negative),
    )
    estimator = HistGradientBoostingClassifier(
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
    scores = estimator.predict_proba(x_calibration)[:, 1]
    threshold, count, wins, lower = calibrate_threshold(scores, y_calibration)
    return DirectionModel(
        estimator=estimator,
        threshold=threshold,
        calibration_count=count,
        calibration_wins=wins,
        calibration_lower_bound=lower,
        direction=direction,
    )


def build_labeled_samples(
    *,
    features: pd.DataFrame,
    bars: pd.DataFrame,
    minutes: pd.DataFrame,
    start_ms: int,
    end_ms: int,
    direction: int,
) -> tuple[np.ndarray, np.ndarray]:
    feature_columns = [column for column in features.columns if column != "end_time_ms"]
    rows: list[np.ndarray] = []
    labels: list[int] = []
    for index in range(0, len(bars), SAMPLE_EVERY_BARS):
        timestamp = int(bars.iloc[index].end_time_ms)
        if not start_ms <= timestamp < end_ms:
            continue
        values = features.iloc[index][feature_columns].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            continue
        labeled = label_retest(minutes, bars, index, direction)
        if labeled is None:
            continue
        rows.append(values)
        labels.append(labeled.won)
    if not rows:
        return np.empty((0, len(feature_columns))), np.empty(0, dtype=int)
    return np.vstack(rows), np.asarray(labels, dtype=int)


def model_manifest(model: DirectionModel) -> dict[str, Any]:
    return {
        "direction": model.direction,
        "model": "HistGradientBoostingClassifier",
        "threshold": model.threshold,
        "calibration_count": model.calibration_count,
        "calibration_wins": model.calibration_wins,
        "calibration_lower_bound": model.calibration_lower_bound,
    }


def derive_v31(
    *, week_start: date, prepared_root: Path, output_manifest: Path
) -> list[dict[str, Any]]:
    evaluation_start = datetime.combine(
        week_start, datetime.min.time(), tzinfo=timezone.utc
    )
    evaluation_end = evaluation_start + timedelta(days=7)
    fit_start = evaluation_start - timedelta(days=TRAINING_DAYS)
    calibration_start = evaluation_start - timedelta(days=CALIBRATION_DAYS)
    minutes, sources = load_minutes(
        start=fit_start - timedelta(days=3),
        end=evaluation_end + timedelta(days=1),
        root=prepared_root / "walk_forward_data",
    )
    bars = aggregate_bars(minutes)
    features = feature_frame(bars)
    fit_start_ms = int(fit_start.timestamp() * 1000)
    calibration_start_ms = int(calibration_start.timestamp() * 1000)
    evaluation_start_ms = int(evaluation_start.timestamp() * 1000)
    evaluation_end_ms = int(evaluation_end.timestamp() * 1000)
    gap_ms = OUTCOME_HORIZON_MINUTES * 60_000

    models: dict[int, DirectionModel] = {}
    sample_counts: dict[str, Any] = {}
    for direction in (1, -1):
        x_fit, y_fit = build_labeled_samples(
            features=features,
            bars=bars,
            minutes=minutes,
            start_ms=fit_start_ms,
            end_ms=calibration_start_ms - gap_ms,
            direction=direction,
        )
        x_cal, y_cal = build_labeled_samples(
            features=features,
            bars=bars,
            minutes=minutes,
            start_ms=calibration_start_ms,
            end_ms=evaluation_start_ms - gap_ms,
            direction=direction,
        )
        models[direction] = train_direction(x_fit, y_fit, x_cal, y_cal, direction)
        sample_counts[str(direction)] = {
            "fit_samples": len(y_fit),
            "fit_win_rate": float(y_fit.mean()) if len(y_fit) else 0.0,
            "calibration_samples": len(y_cal),
            "calibration_win_rate": float(y_cal.mean()) if len(y_cal) else 0.0,
        }

    feature_columns = [column for column in features.columns if column != "end_time_ms"]
    signals: list[dict[str, Any]] = []
    no_trade: dict[str, int] = {}
    last_signal_ms = -10**30
    for index in range(0, len(bars), SAMPLE_EVERY_BARS):
        event = bars.iloc[index]
        timestamp = int(event.end_time_ms)
        if not evaluation_start_ms <= timestamp < evaluation_end_ms:
            continue
        values = features.iloc[index][feature_columns].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            continue
        score_long = models[1].score(values)
        score_short = models[-1].score(values)
        passes = [
            direction
            for direction, score in ((1, score_long), (-1, score_short))
            if score >= models[direction].threshold
        ]
        if not passes:
            no_trade["NO_CALIBRATED_DIRECTION"] = no_trade.get("NO_CALIBRATED_DIRECTION", 0) + 1
            continue
        if len(passes) == 2:
            ratio_long = score_long / max(models[1].threshold, 1e-9)
            ratio_short = score_short / max(models[-1].threshold, 1e-9)
            if abs(ratio_long - ratio_short) < 0.05:
                no_trade["AMBIGUOUS_DIRECTIONAL_SCORES"] = no_trade.get(
                    "AMBIGUOUS_DIRECTIONAL_SCORES", 0
                ) + 1
                continue
            direction = 1 if ratio_long > ratio_short else -1
        else:
            direction = passes[0]
        retest, zone_mid = find_retest(minutes, bars, index, direction)
        if retest is None or zone_mid is None:
            no_trade["PULLBACK_DEFENSE_UNRESOLVED"] = no_trade.get(
                "PULLBACK_DEFENSE_UNRESOLVED", 0
            ) + 1
            continue
        signal_ms = int(retest.open_time_ms) + 60_000
        if signal_ms - last_signal_ms < MIN_SIGNAL_SEPARATION_MINUTES * 60_000:
            no_trade["INDEPENDENT_EVENT_COOLDOWN"] = no_trade.get(
                "INDEPENDENT_EVENT_COOLDOWN", 0
            ) + 1
            continue
        at = float(event.btc_atr)
        stop = (
            min(float(event.btcusdt_futures_low), float(retest.btcusdt_futures_low))
            - STOP_BUFFER_ATR * at
            if direction > 0
            else max(float(event.btcusdt_futures_high), float(retest.btcusdt_futures_high))
            + STOP_BUFFER_ATR * at
        )
        reference = float(retest.btcusdt_futures_close)
        if direction * (reference - stop) <= 0.0:
            continue
        confirm_ns = signal_ms * 1_000_000
        state = "WALK_FORWARD_LONG_AUCTION" if direction > 0 else "WALK_FORWARD_SHORT_AUCTION"
        suffix = hashlib.sha256(
            f"{confirm_ns}|{direction}|{score_long:.12g}|{score_short:.12g}".encode()
        ).hexdigest()[:16]
        signals.append(
            {
                "scenario_id": f"NT-LVCFR-V31-{state}-{suffix}",
                "scenario_kind": state,
                "entry_kind": "CONTINUATION",
                "confirm_time_ns": confirm_ns,
                "eligible_time_ns": confirm_ns,
                "direction": direction,
                "initial_stop": stop,
                "atr": at,
                "first_start_time_ns": timestamp * 1_000_000 - 180_000_000_000,
                "first_end_time_ns": timestamp * 1_000_000,
                "target_mode": "EXISTING_NET_R_OBJECTIVE",
                "disable_rapid_failure_reversal": True,
                "details": {
                    "scenario_kind": state,
                    "entry_kind": "CONTINUATION",
                    "long_score": score_long,
                    "short_score": score_short,
                    "long_threshold": models[1].threshold,
                    "short_threshold": models[-1].threshold,
                    "pullback_zone_mid": zone_mid,
                    "retest_open_time_ms": int(retest.open_time_ms),
                    "target_net_r": TARGET_NET_R,
                    "feature_names": feature_columns,
                    "features": values.tolist(),
                    "fit_start": fit_start.isoformat(),
                    "calibration_start": calibration_start.isoformat(),
                },
            }
        )
        last_signal_ms = signal_ms

    (prepared_root / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for signal in signals:
        state = str(signal["scenario_kind"])
        counts[state] = counts.get(state, 0) + 1
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v31-walk-forward-auction-forest",
        "engine_status": "causal_walk_forward_nonlinear_schedule_only_no_backtest",
        "week_start": week_start.isoformat(),
        "fit_start": fit_start.isoformat(),
        "calibration_start": calibration_start.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "sample_counts": sample_counts,
        "long_model": model_manifest(models[1]),
        "short_model": model_manifest(models[-1]),
        "derived_signal_count": len(signals),
        "signals_per_day": len(signals) / 7.0,
        "state_counts": dict(sorted(counts.items())),
        "no_trade_reasons": dict(sorted(no_trade.items())),
        "target_net_r": TARGET_NET_R,
        "outcome_horizon_minutes": OUTCOME_HORIZON_MINUTES,
        "feature_count": len(feature_columns),
        "feature_names": feature_columns,
        "source_files": sources,
        "selection_policy": (
            "fixed nonlinear model and features; 150-day fit plus disjoint "
            "30-day chronological calibration; cost-aware labels only before "
            "evaluation; completed BTC futures/spot pullback defense; no "
            "evaluation outcomes or parameter search"
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
    signals = derive_v31(
        week_start=args.week_start,
        prepared_root=args.prepared_root.resolve(),
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"candidate": "V31", "signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
