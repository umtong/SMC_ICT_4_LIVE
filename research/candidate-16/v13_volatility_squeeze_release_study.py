#!/usr/bin/env python3
"""External BB-Keltner squeeze-release study for Candidate 16 v13.

The candidate implements a complete volatility-compression auction rather than
an indicator crossover:

    5-minute Bollinger Bands remain inside Keltner Channels for >= 6 bars
      -> compression state only; no order
      -> first release bar closes outside the Keltner Channel with >1.5x prior
         20-bar volume, aligned perpetual taker flow and spot return
      -> release state only; no order
      -> first later counter-direction bar touches the released Keltner edge on
         lower volume and closes beyond the original squeeze edge
      -> pullback state only; no order
      -> strictly later bar breaks the pullback with aligned flow and spot
      -> entry at completed resumption close
      -> invalidation beyond the pullback extreme
      -> objective is one complete squeeze range projected from its edge

The 20/2 Bollinger, 20/1.5 Keltner, six-bar minimum and 1.5x volume release are
reused directly from public squeeze playbooks.  The system does not add an EMA,
RSI or score filter to rescue losses.  Entry, stop and measured objective all
belong to the new post-compression leg.

Checksum-verified Binance Vision spot and USD-M one-minute bars are aggregated
causally to completed five-minute observations.  Outcome paths are evaluated on
one-minute bars with stop-before-target ordering, the project's 20 bp round-trip
cost and one global active trade.  This is a mechanism/geometry study only; no
fills, account, portfolio or NAV are created.  Untouched 2024 remains unopened
unless unchanged 2023 passes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from v10_open_drive_study import DEVELOPMENT_YEAR
from v10_open_drive_study import GLOBAL_ENTRY_CLUSTER_MINUTES
from v10_open_drive_study import HOLDOUT_YEAR
from v10_open_drive_study import ROUND_TRIP_COST_RATE
from v10_open_drive_study import SYMBOLS
from v10_open_drive_study import load_symbol
from v10_open_drive_study import promotion_checks
from v10_open_drive_study import summarize


SIGNAL_MINUTES = 5
BAND_PERIOD = 20
BOLLINGER_STD = 2.0
KELTNER_ATR_MULTIPLIER = 1.5
MIN_SQUEEZE_BARS = 6
BREAKOUT_VOLUME_MULTIPLIER = 1.5
PULLBACK_SEARCH_BARS = 6
RESUMPTION_SEARCH_BARS = 3
MAX_HOLD_MINUTES = 120
MIN_TARGET_NET_R = 1.0


@dataclass(frozen=True, slots=True)
class SqueezeCandidate:
    symbol: str
    squeeze_start_ts: pd.Timestamp
    squeeze_end_ts: pd.Timestamp
    release_ts: pd.Timestamp
    pullback_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    side: int
    entry: float
    stop: float
    target: float
    target_source: str
    planned_loss_rate: float
    target_net_r: float
    squeeze_high: float
    squeeze_low: float
    squeeze_range: float
    squeeze_bars: int
    release_close: float
    release_volume_ratio: float
    release_flow: float
    release_spot_return: float
    pullback_volume_ratio: float
    release_score: float


@dataclass(frozen=True, slots=True)
class ScoredSqueeze:
    candidate: SqueezeCandidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def aggregate_five_minute(panel: pd.DataFrame) -> pd.DataFrame:
    source = panel.copy()
    source.index = pd.to_datetime(source.index, utc=True).astype("datetime64[ns, UTC]")
    grouped = source.resample(
        f"{SIGNAL_MINUTES}min",
        label="left",
        closed="left",
    )
    result = pd.DataFrame(
        {
            "perp_open": grouped["perp_open"].first(),
            "perp_high": grouped["perp_high"].max(),
            "perp_low": grouped["perp_low"].min(),
            "perp_close": grouped["perp_close"].last(),
            "perp_quote_volume": grouped["perp_quote_volume"].sum(),
            "perp_taker_buy_quote": grouped["perp_taker_buy_quote"].sum(),
            "spot_open": grouped["spot_open"].first(),
            "spot_high": grouped["spot_high"].max(),
            "spot_low": grouped["spot_low"].min(),
            "spot_close": grouped["spot_close"].last(),
            "spot_quote_volume": grouped["spot_quote_volume"].sum(),
            "minute_count": grouped["perp_close"].count(),
        },
    )
    result = result[result["minute_count"] == SIGNAL_MINUTES].copy()
    result.index = (result.index + pd.Timedelta(minutes=SIGNAL_MINUTES - 1)).as_unit("ns")
    result["minute"] = result.index
    result["perp_flow"] = (
        2.0
        * result["perp_taker_buy_quote"]
        / result["perp_quote_volume"].replace(0.0, np.nan)
        - 1.0
    )
    result["spot_return"] = np.log(result["spot_close"] / result["spot_open"])

    close = result["perp_close"]
    middle = close.rolling(BAND_PERIOD, min_periods=BAND_PERIOD).mean()
    std = close.rolling(BAND_PERIOD, min_periods=BAND_PERIOD).std(ddof=0)
    result["bb_upper"] = middle + BOLLINGER_STD * std
    result["bb_lower"] = middle - BOLLINGER_STD * std
    ema = close.ewm(span=BAND_PERIOD, adjust=False, min_periods=BAND_PERIOD).mean()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            result["perp_high"] - result["perp_low"],
            (result["perp_high"] - previous_close).abs(),
            (result["perp_low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(BAND_PERIOD, min_periods=BAND_PERIOD).mean()
    result["kc_middle"] = ema
    result["kc_upper"] = ema + KELTNER_ATR_MULTIPLIER * atr
    result["kc_lower"] = ema - KELTNER_ATR_MULTIPLIER * atr
    result["squeeze_on"] = (
        result["bb_upper"].lt(result["kc_upper"])
        & result["bb_lower"].gt(result["kc_lower"])
    ).fillna(False)
    blocks = (~result["squeeze_on"]).cumsum()
    result["squeeze_streak"] = (
        result["squeeze_on"].groupby(blocks).cumsum().astype(int)
    )
    result["prior_volume_mean"] = (
        result["perp_quote_volume"]
        .rolling(BAND_PERIOD, min_periods=BAND_PERIOD)
        .mean()
        .shift(1)
    )
    return result


def detect_candidates(symbol: str, signal: pd.DataFrame) -> list[SqueezeCandidate]:
    candidates: list[SqueezeCandidate] = []
    for position in range(BAND_PERIOD + MIN_SQUEEZE_BARS, len(signal)):
        current = signal.iloc[position]
        previous = signal.iloc[position - 1]
        streak = int(previous["squeeze_streak"])
        if (
            not bool(previous["squeeze_on"])
            or streak < MIN_SQUEEZE_BARS
            or bool(current["squeeze_on"])
        ):
            continue
        close = float(current["perp_close"])
        if close > float(current["kc_upper"]):
            side = 1
        elif close < float(current["kc_lower"]):
            side = -1
        else:
            continue
        volume_mean = float(current["prior_volume_mean"])
        volume = float(current["perp_quote_volume"])
        volume_ratio = volume / max(volume_mean, 1e-12)
        flow = float(current["perp_flow"])
        spot_return = float(current["spot_return"])
        body = side * (close - float(current["perp_open"]))
        if not (
            body > 0.0
            and math.isfinite(volume_ratio)
            and volume_ratio >= BREAKOUT_VOLUME_MULTIPLIER
            and math.isfinite(flow)
            and side * flow > 0.0
            and math.isfinite(spot_return)
            and side * spot_return > 0.0
        ):
            continue

        squeeze_start_position = position - streak
        squeeze_slice = signal.iloc[squeeze_start_position:position]
        squeeze_high = float(squeeze_slice["perp_high"].max())
        squeeze_low = float(squeeze_slice["perp_low"].min())
        squeeze_range = squeeze_high - squeeze_low
        if not math.isfinite(squeeze_range) or squeeze_range <= 0.0:
            continue
        target = (
            squeeze_high + squeeze_range
            if side > 0
            else squeeze_low - squeeze_range
        )
        if (side > 0 and close >= target) or (side < 0 and close <= target):
            continue

        pullback_position: int | None = None
        pullback_row: pd.Series | None = None
        search_end = min(position + 1 + PULLBACK_SEARCH_BARS, len(signal))
        for candidate_position in range(position + 1, search_end):
            row = signal.iloc[candidate_position]
            high = float(row["perp_high"])
            low = float(row["perp_low"])
            candidate_close = float(row["perp_close"])
            if (side > 0 and high >= target) or (side < 0 and low <= target):
                pullback_position = None
                break
            if (side > 0 and candidate_close < squeeze_low) or (
                side < 0 and candidate_close > squeeze_high
            ):
                pullback_position = None
                break
            counter_body = side * (candidate_close - float(row["perp_open"])) < 0.0
            touched_channel = (
                low <= float(row["kc_upper"])
                if side > 0
                else high >= float(row["kc_lower"])
            )
            closes_beyond_edge = (
                candidate_close > squeeze_high
                if side > 0
                else candidate_close < squeeze_low
            )
            lower_volume = float(row["perp_quote_volume"]) < volume
            if counter_body and touched_channel:
                # The first actual channel touch is final; a close back inside
                # the squeeze edge is a head fake, not permission to wait.
                if not closes_beyond_edge or not lower_volume:
                    pullback_position = None
                    break
                pullback_position = candidate_position
                pullback_row = row
                break
        if pullback_position is None or pullback_row is None:
            continue

        pullback_extreme = (
            float(pullback_row["perp_low"])
            if side > 0
            else float(pullback_row["perp_high"])
        )
        pullback_break = (
            float(pullback_row["perp_high"])
            if side > 0
            else float(pullback_row["perp_low"])
        )
        resumption_end = min(
            pullback_position + 1 + RESUMPTION_SEARCH_BARS,
            len(signal),
        )
        for resumption_position in range(pullback_position + 1, resumption_end):
            row = signal.iloc[resumption_position]
            high = float(row["perp_high"])
            low = float(row["perp_low"])
            resumption_close = float(row["perp_close"])
            if (side > 0 and low <= pullback_extreme) or (
                side < 0 and high >= pullback_extreme
            ):
                break
            if (side > 0 and high >= target) or (side < 0 and low <= target):
                break
            resumed = (
                resumption_close > pullback_break
                if side > 0
                else resumption_close < pullback_break
            )
            resumption_body = side * (
                resumption_close - float(row["perp_open"])
            )
            resumption_flow = float(row["perp_flow"])
            resumption_spot = float(row["spot_return"])
            if not (
                resumed
                and resumption_body > 0.0
                and math.isfinite(resumption_flow)
                and side * resumption_flow > 0.0
                and math.isfinite(resumption_spot)
                and side * resumption_spot > 0.0
            ):
                continue
            entry = resumption_close
            stop = pullback_extreme
            if (side > 0 and not stop < entry < target) or (
                side < 0 and not target < entry < stop
            ):
                break
            planned_loss_rate = side * (entry - stop) / entry + ROUND_TRIP_COST_RATE
            net_target_return = side * (target - entry) / entry - ROUND_TRIP_COST_RATE
            if planned_loss_rate <= 0.0 or net_target_return <= 0.0:
                break
            target_net_r = net_target_return / planned_loss_rate
            if target_net_r + 1e-12 < MIN_TARGET_NET_R:
                break
            pullback_volume_ratio = float(
                pullback_row["perp_quote_volume"] / max(volume, 1e-12),
            )
            score = (
                streak
                * volume_ratio
                * abs(close - (squeeze_high if side > 0 else squeeze_low))
                / squeeze_range
            )
            candidates.append(
                SqueezeCandidate(
                    symbol=symbol,
                    squeeze_start_ts=pd.Timestamp(signal.iloc[squeeze_start_position].name),
                    squeeze_end_ts=pd.Timestamp(previous.name),
                    release_ts=pd.Timestamp(current.name),
                    pullback_ts=pd.Timestamp(pullback_row.name),
                    entry_ts=pd.Timestamp(row.name),
                    side=side,
                    entry=entry,
                    stop=stop,
                    target=target,
                    target_source="ONE_SQUEEZE_RANGE_MEASURED_MOVE",
                    planned_loss_rate=planned_loss_rate,
                    target_net_r=target_net_r,
                    squeeze_high=squeeze_high,
                    squeeze_low=squeeze_low,
                    squeeze_range=squeeze_range,
                    squeeze_bars=streak,
                    release_close=close,
                    release_volume_ratio=volume_ratio,
                    release_flow=flow,
                    release_spot_return=spot_return,
                    pullback_volume_ratio=pullback_volume_ratio,
                    release_score=score,
                ),
            )
            break
    return candidates


def collapse_global_clusters(candidates: list[SqueezeCandidate]) -> list[SqueezeCandidate]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.entry_ts)
    clusters: list[list[SqueezeCandidate]] = []
    current: list[SqueezeCandidate] = []
    anchor: pd.Timestamp | None = None
    for candidate in ordered:
        if (
            anchor is None
            or candidate.entry_ts - anchor > pd.Timedelta(minutes=GLOBAL_ENTRY_CLUSTER_MINUTES)
        ):
            if current:
                clusters.append(current)
            current = [candidate]
            anchor = candidate.entry_ts
        else:
            current.append(candidate)
    if current:
        clusters.append(current)
    return [
        max(
            cluster,
            key=lambda item: (
                item.release_score,
                item.target_net_r,
                item.symbol,
            ),
        )
        for cluster in clusters
    ]


def _minute_window(
    panel: pd.DataFrame,
    start: pd.Timestamp,
    minutes: int,
) -> pd.DataFrame | None:
    expected = pd.date_range(start, periods=minutes, freq="min", tz="UTC").as_unit("ns")
    sample = panel.reindex(expected)
    if sample["perp_close"].isna().any():
        return None
    return sample


def score_candidate(candidate: SqueezeCandidate, panel: pd.DataFrame) -> ScoredSqueeze | None:
    future = _minute_window(
        panel,
        candidate.entry_ts + pd.Timedelta(minutes=1),
        MAX_HOLD_MINUTES,
    )
    if future is None:
        return None
    exit_reason = "TIME_EXIT"
    exit_price = float(future.iloc[-1]["perp_close"])
    exit_ts = pd.Timestamp(future.iloc[-1].name)
    mfe = 0.0
    mae = 0.0
    for _, row in future.iterrows():
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        if candidate.side > 0:
            mfe = max(mfe, high / candidate.entry - 1.0)
            mae = min(mae, low / candidate.entry - 1.0)
            stop_hit = low <= candidate.stop
            target_hit = high >= candidate.target
        else:
            mfe = max(mfe, 1.0 - low / candidate.entry)
            mae = min(mae, 1.0 - high / candidate.entry)
            stop_hit = high >= candidate.stop
            target_hit = low <= candidate.target
        if stop_hit:
            exit_reason = "STOP"
            exit_price = candidate.stop
            exit_ts = pd.Timestamp(row.name)
            break
        if target_hit:
            exit_reason = "SQUEEZE_MEASURED_TARGET"
            exit_price = candidate.target
            exit_ts = pd.Timestamp(row.name)
            break
    net_return = (
        candidate.side * (exit_price - candidate.entry) / candidate.entry
        - ROUND_TRIP_COST_RATE
    )
    return ScoredSqueeze(
        candidate=candidate,
        exit_ts=exit_ts,
        exit_reason=exit_reason,
        exit_price=exit_price,
        net_return=net_return,
        net_r=net_return / candidate.planned_loss_rate,
        mfe=mfe,
        mae=mae,
    )


def enforce_one_global_slot(
    candidates: list[SqueezeCandidate],
    panels: dict[str, pd.DataFrame],
) -> tuple[list[ScoredSqueeze], int]:
    active_until: pd.Timestamp | None = None
    scored: list[ScoredSqueeze] = []
    conflicts = 0
    for candidate in sorted(candidates, key=lambda item: item.entry_ts):
        if active_until is not None and candidate.entry_ts <= active_until:
            conflicts += 1
            continue
        result = score_candidate(candidate, panels[candidate.symbol])
        if result is None:
            continue
        scored.append(result)
        active_until = result.exit_ts
    return scored, conflicts


def records(scored: list[ScoredSqueeze]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in scored:
        rows.append(
            {
                **asdict(item.candidate),
                "session": "ROLLING_5M",
                "exit_ts": item.exit_ts,
                "exit_reason": item.exit_reason,
                "exit_price": item.exit_price,
                "net_return": item.net_return,
                "net_r": item.net_r,
                "mfe": item.mfe,
                "mae": item.mae,
            },
        )
    return pd.DataFrame(rows)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(cache: Path, output: Path) -> dict[str, Any]:
    panels = {symbol: load_symbol(symbol, cache) for symbol in SYMBOLS}
    signals = {symbol: aggregate_five_minute(panel) for symbol, panel in panels.items()}
    raw_candidates = [
        candidate
        for symbol, signal in signals.items()
        for candidate in detect_candidates(symbol, signal)
    ]
    selected = collapse_global_clusters(raw_candidates)
    scored, conflicts = enforce_one_global_slot(selected, panels)
    frame = records(scored)
    if frame.empty:
        development = frame
        holdout = frame
    else:
        years = pd.to_datetime(frame["entry_ts"], utc=True).dt.year
        development = frame[years == DEVELOPMENT_YEAR].copy()
        holdout = frame[years == HOLDOUT_YEAR].copy()
    development_summary = summarize(development)
    development_checks = promotion_checks(development_summary)
    development_pass = all(development_checks.values())
    if development_pass:
        holdout_opened = True
        holdout_summary = summarize(holdout)
        holdout_checks = promotion_checks(holdout_summary)
        holdout_pass = all(holdout_checks.values())
    else:
        holdout_opened = False
        holdout_summary = None
        holdout_checks = None
        holdout_pass = False

    if development_pass and holdout_pass:
        decision = "PROMOTE_SQUEEZE_RELEASE_TO_NAUTILUS_CONTINUOUS_ACCOUNT"
    elif development_pass:
        decision = "DISCARD_SQUEEZE_RELEASE_AFTER_UNTOUCHED_2024_FAILURE"
    else:
        decision = "DISCARD_SQUEEZE_RELEASE_AFTER_2023_DEVELOPMENT_FAILURE"

    output.mkdir(parents=True, exist_ok=True)
    development.to_csv(output / "development_trades.csv", index=False)
    if holdout_opened:
        holdout.to_csv(output / "holdout_trades.csv", index=False)
    result = {
        "schema": "candidate-16-v13-volatility-squeeze-release-study-v1",
        "role": "mechanism and geometry study; no fills, account, portfolio, or NAV claim",
        "external_policy": {
            "family": "Bollinger Band / Keltner Channel volatility squeeze",
            "bollinger": {"period": BAND_PERIOD, "standard_deviations": BOLLINGER_STD},
            "keltner": {"period": BAND_PERIOD, "atr_multiplier": KELTNER_ATR_MULTIPLIER},
            "minimum_squeeze_bars": MIN_SQUEEZE_BARS,
            "breakout_volume_multiplier": BREAKOUT_VOLUME_MULTIPLIER,
            "transition": (
                "first lower-volume counter bar touches released KC edge and closes "
                "beyond squeeze edge; strictly later flow/spot-aligned break"
            ),
            "objective": "one complete squeeze range projected from released edge",
        },
        "data": {
            "source": "checksum-verified Binance Vision spot and USD-M 1m monthly klines",
            "signal_cadence_minutes": SIGNAL_MINUTES,
            "execution_path_cadence_minutes": 1,
            "symbols": list(SYMBOLS),
            "years": [DEVELOPMENT_YEAR, HOLDOUT_YEAR],
        },
        "scenario_contract": {
            "no_order_during_squeeze": True,
            "no_order_on_release": True,
            "first_channel_touch_is_final": True,
            "no_order_on_pullback": True,
            "minimum_target_net_r": MIN_TARGET_NET_R,
            "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
            "same_bar_stop_before_target": True,
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "global_entry_or_position_slot": 1,
        },
        "signal_rows": {symbol: int(len(value)) for symbol, value in signals.items()},
        "raw_complete_candidates": len(raw_candidates),
        "global_cluster_representatives": len(selected),
        "global_slot_conflicts_skipped": conflicts,
        "development": {
            "period": "2023-01-01 through 2023-12-31",
            "summary": development_summary,
            "checks": development_checks,
            "passed": development_pass,
        },
        "holdout": {
            "period": "2024-01-01 through 2024-12-31",
            "opened": holdout_opened,
            "summary": holdout_summary,
            "checks": holdout_checks,
            "passed": holdout_pass,
        },
        "promote": development_pass and holdout_pass,
        "decision": decision,
    }
    write_json(output / "study.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.cache.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
