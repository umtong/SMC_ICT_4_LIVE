#!/usr/bin/env python3
"""V28 causal structural-state model with retest confirmation.

V27 could not identify a reliable +2R subset, even after the single permitted
rank-calibration ablation. V28 does not learn trade PnL. It learns only which
side of a completed range-breach auction produces the first subsequent 0.75 ATR
structural expansion over the next 30 minutes. Models are trained on the 120
calendar days preceding each evaluation week. The evaluation week contributes
no labels.

A model prediction is not an entry. Continuation requires a later completed
one-minute retest of the breached boundary with futures/spot aggressive flow
back in the predicted direction. Reversal requires a completed reclaim and
failed retest of the breached boundary with opposite futures/spot flow. The
next native quote is the earliest possible entry.

This module creates a causal signal schedule only. NautilusTrader remains solely
responsible for orders, fills, fees, funding, positions and NAV.
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

from derive_nt_lvcfr_v27_signals import (
    CALIBRATION_FRACTION,
    FEATURE_NAMES,
    MIN_CALIBRATION_SIGNALS,
    Model,
    Event,
    aggregate_five,
    fit_logistic,
    load_minutes,
    make_events,
    predict_matrix,
    wilson_lower,
)

TRAINING_DAYS = 120
STRUCTURAL_BARRIER_ATR = 0.75
STRUCTURAL_HORIZON_5M = 6
RETEST_EXPIRY_MINUTES = 30
STOP_BUFFER_ATR = 0.20
RELIABILITY_FLOOR = 0.55
SELECTION_FRACTIONS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)


@dataclass(frozen=True, slots=True)
class StateModel:
    model: Model
    state: str


def train_rank_model(x: np.ndarray, y: np.ndarray, state: str) -> StateModel:
    if len(y) < 60 or len(np.unique(y)) < 2:
        raise ValueError(f"insufficient diverse {state} labels")
    split = max(40, int(len(y) * (1.0 - CALIBRATION_FRACTION)))
    split = min(split, len(y) - 20)
    mean, scale, weights = fit_logistic(x[:split], y[:split])
    scores = predict_matrix(x[split:], mean, scale, weights)
    labels = y[split:]
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
    return StateModel(
        model=Model(
            mean=mean,
            scale=scale,
            weights=weights,
            threshold=threshold,
            calibration_count=selected_count,
            calibration_wins=selected_wins,
            calibration_lower_bound=selected_lower,
        ),
        state=state,
    )


def event_index_map(bars: pd.DataFrame) -> dict[int, int]:
    return {
        int(value): int(index)
        for index, value in enumerate(bars.end_time_ms.to_numpy(dtype=np.int64))
    }


def structural_label(
    bars: pd.DataFrame,
    indices: dict[int, int],
    event: Event,
) -> int:
    """Return +1 continuation, -1 reversal, or 0 unresolved/ambiguous."""
    index = indices.get(event.confirmation_end_ms)
    if index is None or index + 1 >= len(bars):
        return 0
    confirmation_close = float(bars.iloc[index].futures_close)
    upper = confirmation_close + STRUCTURAL_BARRIER_ATR * event.atr
    lower = confirmation_close - STRUCTURAL_BARRIER_ATR * event.atr
    future = bars.iloc[index + 1 : index + 1 + STRUCTURAL_HORIZON_5M]
    for row in future.itertuples(index=False):
        high_hit = float(row.futures_high) >= upper
        low_hit = float(row.futures_low) <= lower
        if high_hit and low_hit:
            return 0
        if high_hit:
            return event.side
        if low_hit:
            return -event.side
    return 0


def minute_flow(row: Any, market: str) -> float:
    quote = float(getattr(row, f"{market}_quote"))
    buy = float(getattr(row, f"{market}_taker_buy_quote"))
    return (2.0 * buy - quote) / quote if quote > 0.0 else 0.0


def find_retest(
    minutes: pd.DataFrame,
    event: Event,
    direction: int,
) -> tuple[Any | None, str]:
    start_ms = event.confirmation_end_ms
    end_ms = start_ms + RETEST_EXPIRY_MINUTES * 60_000
    future = minutes[
        (minutes.open_time_ms >= start_ms)
        & (minutes.open_time_ms < end_ms)
    ]
    if future.empty:
        return None, "RETEST_DATA_UNAVAILABLE"

    if direction == event.side:
        # Continuation is accepted only after a later pullback into the lower
        # half of the level-to-confirmation zone and a completed defense.
        confirmation_close = float(
            minutes.loc[
                minutes.open_time_ms == event.confirmation_end_ms - 60_000,
                "futures_close",
            ].iloc[-1]
        )
        zone_mid = (event.level + confirmation_close) / 2.0
        for row in future.itertuples(index=False):
            futures_flow = direction * minute_flow(row, "futures")
            spot_flow = direction * minute_flow(row, "spot")
            body = direction * (float(row.futures_close) - float(row.futures_open))
            if direction > 0:
                touched = float(row.futures_low) <= zone_mid
                defended = float(row.futures_close) > event.level
            else:
                touched = float(row.futures_high) >= zone_mid
                defended = float(row.futures_close) < event.level
            if touched and defended and body > 0.0 and futures_flow > 0.0 and spot_flow > 0.0:
                return row, "CONTINUATION_RETEST_DEFENDED"
        return None, "CONTINUATION_RETEST_UNRESOLVED"

    # Reversal must first reclaim the breached boundary, then fail a retest
    # from inside the prior range. The same completed bar can satisfy both.
    reclaimed = False
    for row in future.itertuples(index=False):
        close = float(row.futures_close)
        if event.side > 0:
            reclaimed = reclaimed or close < event.level
            touched = float(row.futures_high) >= event.level
            rejected = close < event.level
        else:
            reclaimed = reclaimed or close > event.level
            touched = float(row.futures_low) <= event.level
            rejected = close > event.level
        futures_flow = direction * minute_flow(row, "futures")
        spot_flow = direction * minute_flow(row, "spot")
        body = direction * (close - float(row.futures_open))
        if reclaimed and touched and rejected and body > 0.0 and futures_flow > 0.0 and spot_flow > 0.0:
            return row, "REVERSAL_RECLAIM_RETEST_REJECTED"
    return None, "REVERSAL_RETEST_UNRESOLVED"


def causal_stop(event: Event, retest: Any, direction: int) -> float:
    if direction == event.side:
        return (
            min(event.level, float(retest.futures_low)) - STOP_BUFFER_ATR * event.atr
            if direction > 0
            else max(event.level, float(retest.futures_high)) + STOP_BUFFER_ATR * event.atr
        )
    return (
        event.event_low - STOP_BUFFER_ATR * event.atr
        if direction > 0
        else event.event_high + STOP_BUFFER_ATR * event.atr
    )


def model_manifest(model: StateModel) -> dict[str, Any]:
    value = model.model
    return {
        "state": model.state,
        "threshold": value.threshold,
        "calibration_count": value.calibration_count,
        "calibration_wins": value.calibration_wins,
        "calibration_lower_bound": value.calibration_lower_bound,
    }


def derive_v28(
    *,
    week_start: date,
    prepared_root: Path,
    output_manifest: Path,
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
    continuation = train_rank_model(
        x,
        (labels == 1).astype(float),
        "STRUCTURAL_CONTINUATION",
    )
    reversal = train_rank_model(
        x,
        (labels == -1).astype(float),
        "STRUCTURAL_REVERSAL",
    )

    signals: list[dict[str, Any]] = []
    no_trade: dict[str, int] = {}
    for event in evaluation_events:
        continuation_probability = continuation.model.predict(event.features)
        reversal_probability = reversal.model.predict(event.features)
        continuation_pass = continuation_probability >= continuation.model.threshold
        reversal_pass = reversal_probability >= reversal.model.threshold
        if continuation_pass and reversal_pass:
            if abs(continuation_probability - reversal_probability) < 0.05:
                no_trade["AMBIGUOUS_STRUCTURAL_STATE"] = no_trade.get(
                    "AMBIGUOUS_STRUCTURAL_STATE", 0
                ) + 1
                continue
            direction = (
                event.side
                if continuation_probability > reversal_probability
                else -event.side
            )
        elif continuation_pass:
            direction = event.side
        elif reversal_pass:
            direction = -event.side
        else:
            no_trade["NO_RELIABLE_STRUCTURAL_STATE"] = no_trade.get(
                "NO_RELIABLE_STRUCTURAL_STATE", 0
            ) + 1
            continue

        retest, reason = find_retest(minutes, event, direction)
        if retest is None:
            no_trade[reason] = no_trade.get(reason, 0) + 1
            continue
        stop = causal_stop(event, retest, direction)
        reference = float(
            retest.futures_close if direction > 0 else retest.futures_close
        )
        if direction * (reference - stop) <= 0.0:
            no_trade["INVALID_RETEST_STOP"] = no_trade.get(
                "INVALID_RETEST_STOP", 0
            ) + 1
            continue
        confirm_ns = (int(retest.open_time_ms) + 60_000) * 1_000_000
        state = (
            "ADAPTIVE_STRUCTURAL_CONTINUATION_RETEST"
            if direction == event.side
            else "ADAPTIVE_STRUCTURAL_REVERSAL_RETEST"
        )
        suffix = hashlib.sha256(
            f"{confirm_ns}|{direction}|{state}|{event.level:.12g}".encode()
        ).hexdigest()[:16]
        details = {
            "scenario_kind": state,
            "entry_kind": "CONTINUATION",
            "event_side": event.side,
            "event_level": event.level,
            "event_high": event.event_high,
            "event_low": event.event_low,
            "retest_open_time_ms": int(retest.open_time_ms),
            "retest_reason": reason,
            "continuation_probability": continuation_probability,
            "reversal_probability": reversal_probability,
            "continuation_threshold": continuation.model.threshold,
            "reversal_threshold": reversal.model.threshold,
            "structural_barrier_atr": STRUCTURAL_BARRIER_ATR,
            "training_window_days": TRAINING_DAYS,
            "feature_names": list(FEATURE_NAMES),
            "features": list(event.features),
        }
        signals.append(
            {
                "scenario_id": f"NT-LVCFR-V28-{state}-{suffix}",
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
                "details": details,
            }
        )

    signals.sort(key=lambda item: (int(item["confirm_time_ns"]), str(item["scenario_id"])))
    (prepared_root / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for signal in signals:
        state = str(signal["scenario_kind"])
        counts[state] = counts.get(state, 0) + 1
    label_counts = {
        "continuation": int((labels == 1).sum()),
        "reversal": int((labels == -1).sum()),
        "unresolved": int((labels == 0).sum()),
    }
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v28-structural-state-retest",
        "engine_status": "causal_structural_model_schedule_only_no_backtest",
        "week_start": week_start.isoformat(),
        "training_start": training_start.isoformat(),
        "training_end": evaluation_start.isoformat(),
        "training_events": len(training_events),
        "training_label_counts": label_counts,
        "evaluation_events": len(evaluation_events),
        "derived_signal_count": len(signals),
        "state_counts": dict(sorted(counts.items())),
        "no_trade_reasons": dict(sorted(no_trade.items())),
        "continuation_model": model_manifest(continuation),
        "reversal_model": model_manifest(reversal),
        "source_manifest": source_manifest,
        "selection_policy": (
            "120-day pre-evaluation structural-state learning; chronological "
            "rank calibration; completed one-minute retest/reclaim defense; "
            "no evaluation labels and no PnL labels"
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
    signals = derive_v28(
        week_start=args.week_start,
        prepared_root=args.prepared_root.resolve(),
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"candidate": "V28", "signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
