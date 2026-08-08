#!/usr/bin/env python3
"""Causal funding-settlement premium-unwind diagnostic.

Funding payment time is the exogenous parent event.  The screen reuses the
verified Candidate 16/Candidate 05 feature clock for futures trades, immutable
L1 pressure, Binance spot participation and five-minute positioning metrics.
It downloads only Binance's checksum-verified public funding-rate archives.

Positive funding means longs pay; negative funding means shorts pay.  A parent
requires the paying side to still own an excess spot/perpetual premium and new
risk immediately before settlement.  A trade is considered only after a
strictly later completed minute shows premium contraction, perpetual-led price
delivery, opposite active flow, falling OI and independent L1 support.

This is an information-value screen.  It does not create a portfolio, fills,
positions, PnL or NAV; a passing result must be promoted to NautilusTrader
before any untouched validation or performance claim.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import time
from typing import Iterable
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

from positioning_features import load_range as load_positioning_range


NS_PER_MINUTE = 60_000_000_000
ROUND_TRIP_COST_BPS = 20.0
MIN_NET_R = 1.15
BASELINE_MINUTES = 480
MIN_BASELINE_MINUTES = 240
MAX_CONFIRM_MINUTES = 3
MAX_HOLD_MINUTES = 180
STOP_BUFFER_BPS = 1.0


@dataclass(frozen=True, slots=True)
class FundingEvent:
    timestamp: pd.Timestamp
    rate: float
    interval_hours: float


@dataclass(frozen=True, slots=True)
class RawFundingFile:
    day: str
    url: str
    local_path: str
    size_bytes: int
    sha256: str
    checksum: str


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _download_checked(
    url: str,
    destination: Path,
    attempts: int = 5,
) -> tuple[Path, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    checksum_path = destination.with_name(destination.name + ".CHECKSUM")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if not destination.exists():
                urllib.request.urlretrieve(url, destination)
            if not checksum_path.exists():
                urllib.request.urlretrieve(url + ".CHECKSUM", checksum_path)
            expected = (
                checksum_path.read_text(encoding="utf-8")
                .strip()
                .split()[0]
                .lower()
            )
            actual = _sha256(destination)
            if actual != expected:
                destination.unlink(missing_ok=True)
                checksum_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"funding archive checksum mismatch: {actual} != {expected}",
                )
            return destination, expected
        except (
            OSError,
            TimeoutError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            RuntimeError,
        ) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            checksum_path.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to obtain {url}") from last_error


def _normalize(name: object) -> str:
    return str(name).strip().lower().replace(" ", "_")


def _read_funding_archive(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    columns = {_normalize(column): column for column in raw.columns}
    timestamp_name = next(
        (
            columns[name]
            for name in ("calc_time", "funding_time", "fundingtime")
            if name in columns
        ),
        None,
    )
    rate_name = next(
        (
            columns[name]
            for name in (
                "last_funding_rate",
                "funding_rate",
                "fundingrate",
            )
            if name in columns
        ),
        None,
    )
    interval_name = next(
        (
            columns[name]
            for name in ("funding_interval_hours", "funding_interval")
            if name in columns
        ),
        None,
    )
    if timestamp_name is None or rate_name is None:
        no_header = pd.read_csv(path, compression="zip", header=None)
        if no_header.shape[1] < 2:
            raise RuntimeError(
                f"unexpected funding archive schema: {list(raw.columns)}",
            )
        if no_header.shape[1] >= 3:
            no_header = no_header.iloc[:, :3]
            no_header.columns = [
                "calc_time",
                "funding_interval_hours",
                "last_funding_rate",
            ]
            timestamp_name = "calc_time"
            interval_name = "funding_interval_hours"
            rate_name = "last_funding_rate"
        else:
            no_header.columns = ["calc_time", "last_funding_rate"]
            timestamp_name = "calc_time"
            interval_name = None
            rate_name = "last_funding_rate"
        raw = no_header

    timestamp_numeric = pd.to_numeric(raw[timestamp_name], errors="raise")
    unit = "us" if abs(float(timestamp_numeric.iloc[0])) > 10**14 else "ms"
    timestamp = pd.to_datetime(timestamp_numeric, unit=unit, utc=True)
    rate = pd.to_numeric(raw[rate_name], errors="raise").astype(float)
    interval = (
        pd.to_numeric(raw[interval_name], errors="coerce").astype(float)
        if interval_name is not None
        else pd.Series(8.0, index=raw.index, dtype=float)
    )
    frame = pd.DataFrame(
        {
            "timestamp": timestamp,
            "rate": rate,
            "interval_hours": interval.fillna(8.0),
        },
    )
    if frame["timestamp"].duplicated().any():
        raise RuntimeError("funding archive contains duplicate timestamps")
    return frame.sort_values("timestamp", kind="stable")


def load_funding(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[list[FundingEvent], list[RawFundingFile]]:
    frames: list[pd.DataFrame] = []
    evidence: list[RawFundingFile] = []
    day = start
    while day <= end:
        stamp = day.isoformat()
        filename = f"{symbol}-fundingRate-{stamp}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/"
            f"fundingRate/{symbol}/{filename}"
        )
        path, expected = _download_checked(
            url,
            cache / "fundingRate" / filename,
        )
        frames.append(_read_funding_archive(path))
        evidence.append(
            RawFundingFile(
                day=stamp,
                url=url,
                local_path=str(path),
                size_bytes=path.stat().st_size,
                sha256=_sha256(path),
                checksum=expected,
            ),
        )
        day += timedelta(days=1)
    frame = pd.concat(frames, ignore_index=True).sort_values(
        "timestamp",
        kind="stable",
    )
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    selected = frame[
        frame["timestamp"].ge(start_ts) & frame["timestamp"].lt(end_ts)
    ].copy()
    if selected.empty:
        raise RuntimeError("funding archive range produced no events")
    if selected["timestamp"].duplicated().any():
        raise RuntimeError("funding range contains duplicate events")
    return [
        FundingEvent(
            timestamp=pd.Timestamp(row.timestamp),
            rate=float(row.rate),
            interval_hours=float(row.interval_hours),
        )
        for row in selected.itertuples(index=False)
    ], evidence


def build_state(feature_path: Path) -> pd.DataFrame:
    frame = pd.read_csv(feature_path, compression="infer")
    required = {
        "observed_time_ns",
        "feature_ready",
        "positioning_feature_ready",
        "basis_bps",
        "basis_change_bps",
        "spot_perp_return_gap_bps",
        "spot_trade_close",
        "perp_trade_close",
        "spot_flow_60s",
        "spot_ret_60s_bps",
        "flow_60s",
        "ret_60s_bps",
        "oi_change_5m",
        "oi_value_change_5m",
        "bt_imbalance_close",
        "bt_microprice_premium_close",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"funding screen feature contract drifted: {missing}")
    observed_ns = pd.to_numeric(
        frame["observed_time_ns"],
        errors="raise",
    ).astype("int64")
    frame["observed_time"] = pd.to_datetime(observed_ns, unit="ns", utc=True)
    frame["minute_start_ns"] = observed_ns // NS_PER_MINUTE * NS_PER_MINUTE
    for column in ("feature_ready", "positioning_feature_ready"):
        if frame[column].dtype != bool:
            frame[column] = (
                frame[column]
                .astype(str)
                .str.strip()
                .str.lower()
                .isin({"true", "1", "yes"})
            )
    numeric = sorted(
        required
        - {
            "observed_time_ns",
            "feature_ready",
            "positioning_feature_ready",
        },
    )
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["basis_baseline_bps"] = (
        frame["basis_bps"]
        .shift(1)
        .rolling(
            BASELINE_MINUTES,
            min_periods=MIN_BASELINE_MINUTES,
        )
        .median()
    )
    if frame["observed_time"].duplicated().any():
        raise RuntimeError("feature observations are duplicated")
    return frame.sort_values("observed_time", kind="stable").reset_index(drop=True)


def build_kline_state(klines: pd.DataFrame) -> pd.DataFrame:
    required = {"open_time_dt", "high", "low", "close"}
    missing = sorted(required - set(klines.columns))
    if missing:
        raise RuntimeError(f"kline contract drifted: {missing}")
    frame = klines.copy()
    frame["minute_start_ns"] = (
        frame["open_time_dt"]
        .astype("datetime64[ns, UTC]")
        .astype("int64")
    )
    for column in ("high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
    if frame["minute_start_ns"].duplicated().any():
        raise RuntimeError("kline minute starts are duplicated")
    return frame.set_index("minute_start_ns").sort_index()


def _distance_bps(entry: float, price: float, side: int) -> float:
    return side * math.log(price / entry) * 10_000.0


def screen(
    features: pd.DataFrame,
    klines: pd.DataFrame,
    funding_events: Iterable[FundingEvent],
    evaluation_start: date,
    evaluation_end: date,
) -> tuple[pd.DataFrame, dict[str, object]]:
    records: list[dict[str, object]] = []
    evaluation_start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    evaluation_end_ts = pd.Timestamp(evaluation_end, tz="UTC") + pd.Timedelta(
        days=1,
    )
    funding_list = [
        event
        for event in funding_events
        if evaluation_start_ts <= event.timestamp < evaluation_end_ts
    ]
    observations = features["observed_time"]

    for sequence, event in enumerate(funding_list):
        position = int(observations.searchsorted(event.timestamp, side="left"))
        pre_index = position - 1
        if pre_index < 0:
            continue
        pre = features.iloc[pre_index]
        payer_direction = 1 if event.rate > 0.0 else -1 if event.rate < 0.0 else 0
        if payer_direction == 0:
            records.append(
                {
                    "funding_time": event.timestamp,
                    "classification": "ZERO_FUNDING_NO_PARENT_DIRECTION",
                    "funding_rate": event.rate,
                },
            )
            continue

        base_details = {
            "funding_time": event.timestamp,
            "funding_rate": event.rate,
            "funding_interval_hours": event.interval_hours,
            "payer_direction": payer_direction,
            "pre_observed_time": pre["observed_time"],
            "pre_basis_bps": float(pre["basis_bps"]),
            "pre_basis_baseline_bps": float(pre["basis_baseline_bps"])
            if math.isfinite(float(pre["basis_baseline_bps"]))
            else None,
            "pre_oi_change_5m": float(pre["oi_change_5m"]),
            "pre_oi_value_change_5m": float(pre["oi_value_change_5m"]),
        }
        ready = bool(pre["feature_ready"]) and bool(
            pre["positioning_feature_ready"],
        )
        baseline = float(pre["basis_baseline_bps"])
        if not ready or not math.isfinite(baseline):
            records.append(
                {
                    **base_details,
                    "classification": "PRE_SETTLEMENT_STATE_NOT_READY",
                },
            )
            continue
        excess_premium = payer_direction * (
            float(pre["basis_bps"]) - baseline
        )
        new_risk = (
            float(pre["oi_change_5m"]) >= 0.0
            and float(pre["oi_value_change_5m"]) >= 0.0
        )
        if excess_premium <= 0.0 or not new_risk:
            records.append(
                {
                    **base_details,
                    "classification": "NO_CROWDED_PAYING_SIDE_PREMIUM",
                    "excess_premium_bps": excess_premium,
                    "pre_new_risk": new_risk,
                },
            )
            continue

        side = -payer_direction
        confirmation_index: int | None = None
        upper = event.timestamp + pd.Timedelta(
            minutes=MAX_CONFIRM_MINUTES,
            seconds=59.999,
        )
        post_start = int(
            observations.searchsorted(event.timestamp, side="right"),
        )
        post_end = int(observations.searchsorted(upper, side="right"))
        for candidate_index in range(post_start, min(post_end, len(features))):
            row = features.iloc[candidate_index]
            if not bool(row["feature_ready"]) or not bool(
                row["positioning_feature_ready"],
            ):
                continue
            basis_contracted = payer_direction * (
                float(row["basis_bps"]) - float(pre["basis_bps"])
            ) < 0.0
            perp_led_unwind = (
                side * float(row["ret_60s_bps"]) > 0.0
                and side * float(row["flow_60s"]) > 0.0
                and side * float(row["spot_perp_return_gap_bps"]) > 0.0
            )
            positioning_cleared = (
                float(row["oi_change_5m"]) < 0.0
                and float(row["oi_value_change_5m"]) < 0.0
            )
            l1_support = (
                side * float(row["bt_imbalance_close"]) > 0.0
                and side * float(row["bt_microprice_premium_close"]) > 0.0
            )
            if (
                basis_contracted
                and perp_led_unwind
                and positioning_cleared
                and l1_support
            ):
                confirmation_index = candidate_index
                break
        if confirmation_index is None:
            records.append(
                {
                    **base_details,
                    "classification": "NO_POST_SETTLEMENT_STATE_TRANSITION",
                    "excess_premium_bps": excess_premium,
                },
            )
            continue

        confirmation = features.iloc[confirmation_index]
        entry = float(confirmation["perp_trade_close"])
        current_spot = float(confirmation["spot_trade_close"])
        target = current_spot * math.exp(baseline / 10_000.0)
        settlement_minute_ns = (
            int(event.timestamp.value) // NS_PER_MINUTE * NS_PER_MINUTE
        )
        confirmation_minute_ns = int(confirmation["minute_start_ns"])
        segment = klines.loc[
            (klines.index >= settlement_minute_ns)
            & (klines.index <= confirmation_minute_ns)
        ]
        if segment.empty:
            records.append(
                {
                    **base_details,
                    "classification": "MISSING_SETTLEMENT_KLINE_GEOMETRY",
                    "confirmation_time": confirmation["observed_time"],
                },
            )
            continue
        adverse_extreme = (
            float(segment["low"].min())
            if side > 0
            else float(segment["high"].max())
        )
        stop = adverse_extreme * math.exp(
            -side * STOP_BUFFER_BPS / 10_000.0,
        )
        target_distance = _distance_bps(entry, target, side)
        stop_distance = _distance_bps(stop, entry, side)
        transition_details = {
            **base_details,
            "classification": "STATE_TRANSITION_ROUTED",
            "excess_premium_bps": excess_premium,
            "side": side,
            "confirmation_time": confirmation["observed_time"],
            "confirmation_basis_bps": float(confirmation["basis_bps"]),
            "confirmation_basis_change_from_pre_bps": float(
                confirmation["basis_bps"] - pre["basis_bps"],
            ),
            "confirmation_perp_return_bps": float(
                confirmation["ret_60s_bps"],
            ),
            "confirmation_spot_return_bps": float(
                confirmation["spot_ret_60s_bps"],
            ),
            "confirmation_return_gap_bps": float(
                confirmation["spot_perp_return_gap_bps"],
            ),
            "confirmation_flow": float(confirmation["flow_60s"]),
            "confirmation_oi_change_5m": float(
                confirmation["oi_change_5m"],
            ),
            "confirmation_oi_value_change_5m": float(
                confirmation["oi_value_change_5m"],
            ),
            "confirmation_l1_imbalance": float(
                confirmation["bt_imbalance_close"],
            ),
            "confirmation_microprice_premium": float(
                confirmation["bt_microprice_premium_close"],
            ),
            "entry": entry,
            "target": target,
            "stop": stop,
            "target_distance_bps": target_distance,
            "stop_distance_bps": stop_distance,
        }
        if target_distance <= ROUND_TRIP_COST_BPS or stop_distance <= 0.0:
            records.append(
                {
                    **transition_details,
                    "classification": "INVALID_EXECUTABLE_GEOMETRY",
                },
            )
            continue
        planned_loss_bps = stop_distance + ROUND_TRIP_COST_BPS
        target_net_bps = target_distance - ROUND_TRIP_COST_BPS
        planned_net_r = target_net_bps / planned_loss_bps
        if planned_net_r < MIN_NET_R:
            records.append(
                {
                    **transition_details,
                    "classification": "INSUFFICIENT_NET_R_AFTER_COSTS",
                    "planned_net_r": planned_net_r,
                },
            )
            continue

        next_funding = (
            funding_list[sequence + 1].timestamp
            if sequence + 1 < len(funding_list)
            else event.timestamp + pd.Timedelta(hours=event.interval_hours)
        )
        latest_exit = min(
            confirmation["observed_time"]
            + pd.Timedelta(minutes=MAX_HOLD_MINUTES),
            next_funding - pd.Timedelta(microseconds=1),
            evaluation_end_ts - pd.Timedelta(microseconds=1),
        )
        start_minute_ns = confirmation_minute_ns + NS_PER_MINUTE
        end_minute_ns = (
            int(pd.Timestamp(latest_exit).value)
            // NS_PER_MINUTE
            * NS_PER_MINUTE
        )
        future = klines.loc[
            (klines.index >= start_minute_ns)
            & (klines.index <= end_minute_ns)
        ]
        if future.empty:
            records.append(
                {
                    **transition_details,
                    "classification": "NO_POST_ENTRY_KLINES",
                    "planned_net_r": planned_net_r,
                },
            )
            continue
        outcome = "TIMEOUT"
        exit_price = float(future.iloc[-1]["close"])
        exit_minute_ns = int(future.index[-1])
        for minute_ns, bar in future.iterrows():
            stop_hit = (
                float(bar["low"]) <= stop
                if side > 0
                else float(bar["high"]) >= stop
            )
            target_hit = (
                float(bar["high"]) >= target
                if side > 0
                else float(bar["low"]) <= target
            )
            if stop_hit:
                outcome = "STOP_FIRST"
                exit_price = stop
                exit_minute_ns = int(minute_ns)
                break
            if target_hit:
                outcome = "TARGET_FIRST"
                exit_price = target
                exit_minute_ns = int(minute_ns)
                break
        gross_bps = _distance_bps(entry, exit_price, side)
        net_bps = gross_bps - ROUND_TRIP_COST_BPS
        records.append(
            {
                **transition_details,
                "classification": "EXECUTABLE",
                "outcome": outcome,
                "planned_loss_bps": planned_loss_bps,
                "planned_net_r": planned_net_r,
                "net_bps": net_bps,
                "realized_r": net_bps / planned_loss_bps,
                "exit_time": pd.to_datetime(
                    exit_minute_ns,
                    unit="ns",
                    utc=True,
                ),
                "hold_minutes": (
                    exit_minute_ns - confirmation_minute_ns
                )
                / NS_PER_MINUTE,
            },
        )

    result = pd.DataFrame(records)
    executable = result[
        result.get("classification", pd.Series(dtype=str)).eq("EXECUTABLE")
    ].copy()
    wins = int(
        executable.get("outcome", pd.Series(dtype=str))
        .eq("TARGET_FIRST")
        .sum()
    )
    losses = int(
        executable.get("outcome", pd.Series(dtype=str))
        .eq("STOP_FIRST")
        .sum()
    )
    timeouts = int(
        executable.get("outcome", pd.Series(dtype=str)).eq("TIMEOUT").sum()
    )
    calendar_days = (evaluation_end - evaluation_start).days + 1
    summary: dict[str, object] = {
        "schema": "candidate-11-funding-settlement-unwind-screen-v1",
        "classification": "DIAGNOSTIC_SCREEN_ONLY",
        "success_claim": False,
        "calendar_days": calendar_days,
        "funding_events": len(funding_list),
        "records": int(len(result)),
        "classification_counts": result.get(
            "classification",
            pd.Series(dtype=str),
        )
        .value_counts()
        .to_dict(),
        "executable_episodes": int(len(executable)),
        "target_first": wins,
        "stop_first": losses,
        "timeouts": timeouts,
        "target_first_rate": (
            wins / len(executable) if len(executable) else 0.0
        ),
        "mean_realized_r": (
            float(executable["realized_r"].mean())
            if len(executable)
            else 0.0
        ),
        "median_realized_r": (
            float(executable["realized_r"].median())
            if len(executable)
            else 0.0
        ),
        "median_planned_net_r": (
            float(executable["planned_net_r"].median())
            if len(executable)
            else 0.0
        ),
        "sum_net_bps": (
            float(executable["net_bps"].sum()) if len(executable) else 0.0
        ),
        "frequency_pass": bool(len(executable) >= calendar_days),
        "screen_pass": bool(
            len(executable) >= calendar_days
            and len(executable) > 0
            and wins / len(executable) >= 0.80
            and float(executable["realized_r"].mean()) > 0.0
        ),
        "parameters": {
            "baseline_minutes": BASELINE_MINUTES,
            "maximum_confirmation_minutes": MAX_CONFIRM_MINUTES,
            "maximum_hold_minutes": MAX_HOLD_MINUTES,
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "minimum_net_r": MIN_NET_R,
            "stop_buffer_bps": STOP_BUFFER_BPS,
        },
    }
    return result, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_start = date.fromisoformat(args.build_start)
    build_end = date.fromisoformat(args.build_end)
    evaluation_start = date.fromisoformat(args.evaluation_start)
    evaluation_end = date.fromisoformat(args.evaluation_end)
    args.output.mkdir(parents=True, exist_ok=True)

    klines, feature_path, raw_files, raw_evidence = load_positioning_range(
        symbol=args.symbol,
        start=build_start,
        end=build_end,
        cache=args.cache,
        output=args.output,
    )
    funding, funding_evidence = load_funding(
        args.symbol,
        build_start,
        build_end,
        args.cache,
    )
    features = build_state(feature_path)
    kline_state = build_kline_state(klines)
    episodes, summary = screen(
        features,
        kline_state,
        funding,
        evaluation_start,
        evaluation_end,
    )
    summary.update(
        {
            "symbol": args.symbol,
            "build_start": build_start.isoformat(),
            "build_end": build_end.isoformat(),
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end": evaluation_end.isoformat(),
            "funding_raw_files": [asdict(item) for item in funding_evidence],
            "reused_feature_path": str(feature_path),
            "reused_raw_file_count": len(raw_files),
            "reused_raw_evidence_count": len(raw_evidence),
        },
    )
    episodes.to_csv(args.output / "episodes.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output / "funding_raw_evidence.json").write_text(
        json.dumps(
            [asdict(item) for item in funding_evidence],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
