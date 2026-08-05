#!/usr/bin/env python3
"""Attach official 10-second cumulative book depth to failed-auction trades.

Binance Vision ``bookDepth`` snapshots report cumulative depth/notional at
±1..±5 percent from the market every ten seconds.  The data are coarse relative
to L2 deltas, but they directly reveal whether liquidity supporting the
expected reversal replenished while opposite-side depth depleted between the
probe and displacement.

Only event dates (plus one prior UTC day for rolling baselines) are downloaded.
All joins are backward as-of; no snapshot after an event can affect its state.
The diagnostic does not alter the production candidate.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from typing import Any
from zipfile import ZipFile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auxiliary_data import AuxiliaryDownload, _download_one  # noqa: E402
from core import AuctionStateMachine, CandidateConfig  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Variant, simulate  # noqa: E402


DEPTH_COLUMNS = ["timestamp", "percentage", "depth", "notional"]
LEVELS = (1, 2, 5)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime, str]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime, str]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7), "quick"

    return [
        week("discovery", str(research["discovery_week"])),
        *[
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        (
            "long-evaluation",
            parse_utc_date(str(research["long_start"])),
            parse_utc_date(str(research["long_end"])),
            "development",
        ),
    ]


def _event_context(bars: list[Any], candidate: CandidateConfig) -> pd.DataFrame:
    machine = AuctionStateMachine(
        candidate,
        instrument_id=f"BTCUSDT-PERP.BINANCE:{candidate.range_minutes}m",
    )
    for item in bars:
        machine.on_bar(item)
    rows: dict[str, dict[str, Any]] = {}
    for event in machine.transitions:
        row = rows.setdefault(event.scenario_id, {"scenario_id": event.scenario_id})
        if event.event_type == "LIQUIDITY_PROBE_REJECTED":
            row.update(
                {
                    "probe_time_ns": event.event_time_ns,
                    "probe_flow_z": event.details.get("flow_z"),
                    "probe_volume_z": event.details.get("volume_z"),
                },
            )
        elif event.event_type == "REVERSAL_DISPLACEMENT_CONFIRMED":
            row.update(
                {
                    "displacement_time_ns": event.event_time_ns,
                    "displacement_flow_z": event.details.get("flow_z"),
                    "displacement_body_atr": event.details.get("body_atr"),
                },
            )
    return pd.DataFrame(rows.values())


def _event_days(frame: pd.DataFrame) -> list[date]:
    values: set[date] = set()
    for column in ("probe_time_ns", "displacement_time_ns", "entry_time_ns"):
        times = pd.to_datetime(frame[column].astype("int64"), unit="ns", utc=True)
        for value in times:
            day = value.date()
            values.add(day)
            values.add(day - timedelta(days=1))
    return sorted(values)


def _download_depth_days(
    days: list[date],
    *,
    cache_dir: Path,
    workers: int,
) -> list[AuxiliaryDownload]:
    records: list[AuxiliaryDownload] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                _download_one,
                data_type="bookDepth",
                symbol="BTCUSDT",
                day=day,
                cache_dir=cache_dir,
            ): day
            for day in days
        }
        for future in as_completed(futures):
            records.append(future.result())
    return sorted(records, key=lambda item: item.day)


def _csv_bytes(record: AuxiliaryDownload) -> bytes:
    with ZipFile(record.path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one depth CSV in {record.path}, got {members}")
        return archive.read(members[0])


def _read_depth(records: list[AuxiliaryDownload]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for record in records:
        frame = pd.read_csv(BytesIO(_csv_bytes(record)), header=None, dtype=str)
        if frame.shape[1] < len(DEPTH_COLUMNS):
            raise ValueError(f"invalid depth schema for {record.day}: {frame.shape}")
        frame = frame.iloc[:, : len(DEPTH_COLUMNS)].copy()
        frame.columns = DEPTH_COLUMNS
        first = str(frame.iloc[0]["timestamp"]).strip().lower()
        if first == "timestamp" or not re.search(r"\d", first):
            frame = frame.iloc[1:].reset_index(drop=True)
        frame["timestamp"] = pd.to_datetime(
            frame["timestamp"],
            utc=True,
            errors="raise",
        ).astype("datetime64[ns, UTC]")
        frame["percentage"] = pd.to_numeric(frame["percentage"], errors="raise").astype(int)
        frame["depth"] = pd.to_numeric(frame["depth"], errors="raise")
        frame["notional"] = pd.to_numeric(frame["notional"], errors="raise")
        frames.append(frame)
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.sort_values(["timestamp", "percentage"], kind="stable")
    raw = raw.drop_duplicates(["timestamp", "percentage"], keep="last")
    wide = raw.pivot(index="timestamp", columns="percentage", values="notional").sort_index()
    required = {-level for level in LEVELS} | set(LEVELS)
    missing = sorted(required - set(int(value) for value in wide.columns))
    if missing:
        raise ValueError(f"bookDepth is missing required levels: {missing}")
    result = pd.DataFrame(index=wide.index)
    for level in LEVELS:
        bid = wide[-level].astype(float)
        ask = wide[level].astype(float)
        result[f"bid_notional_{level}"] = bid
        result[f"ask_notional_{level}"] = ask
        log_ratio = np.log(bid.where(bid > 0.0)) - np.log(ask.where(ask > 0.0))
        result[f"log_bid_ask_{level}"] = log_ratio
        history = log_ratio.rolling(2160, min_periods=360)
        result[f"log_bid_ask_z_{level}"] = (
            log_ratio - history.mean().shift(1)
        ) / history.std(ddof=0).shift(1).replace(0.0, np.nan)
        for side, series in (("bid", bid), ("ask", ask)):
            logged = np.log(series.where(series > 0.0))
            side_history = logged.rolling(2160, min_periods=360)
            result[f"{side}_notional_z_{level}"] = (
                logged - side_history.mean().shift(1)
            ) / side_history.std(ddof=0).shift(1).replace(0.0, np.nan)
    return result.reset_index()


def _asof(times: pd.Series, depth: pd.DataFrame, prefix: str) -> pd.DataFrame:
    events = pd.DataFrame(
        {
            "event_row": np.arange(len(times), dtype=int),
            "event_time": pd.to_datetime(
                times.astype("int64"),
                unit="ns",
                utc=True,
            ).astype("datetime64[ns, UTC]"),
        },
    ).sort_values("event_time", kind="stable")
    joined = pd.merge_asof(
        events,
        depth.sort_values("timestamp", kind="stable"),
        left_on="event_time",
        right_on="timestamp",
        direction="backward",
        allow_exact_matches=True,
        tolerance=pd.Timedelta("30s"),
    )
    joined = joined.sort_values("event_row", kind="stable").reset_index(drop=True)
    drop = [column for column in ("event_row", "event_time") if column in joined]
    joined = joined.drop(columns=drop)
    return joined.rename(columns={column: f"{prefix}_{column}" for column in joined.columns})


def _derive(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    side_sign = result["side"].map({"LONG": 1.0, "SHORT": -1.0}).astype(float)
    result["trade_direction_flow_z"] = result["displacement_flow_z"] * side_sign
    for level in LEVELS:
        result[f"probe_aligned_depth_ratio_{level}"] = (
            result[f"probe_log_bid_ask_{level}"] * side_sign
        )
        result[f"probe_aligned_depth_ratio_z_{level}"] = (
            result[f"probe_log_bid_ask_z_{level}"] * side_sign
        )
        result[f"displacement_aligned_depth_ratio_{level}"] = (
            result[f"displacement_log_bid_ask_{level}"] * side_sign
        )
        result[f"displacement_aligned_depth_ratio_z_{level}"] = (
            result[f"displacement_log_bid_ask_z_{level}"] * side_sign
        )
        long_mask = side_sign > 0.0
        probe_support = np.where(
            long_mask,
            result[f"probe_bid_notional_{level}"],
            result[f"probe_ask_notional_{level}"],
        )
        probe_opposite = np.where(
            long_mask,
            result[f"probe_ask_notional_{level}"],
            result[f"probe_bid_notional_{level}"],
        )
        displacement_support = np.where(
            long_mask,
            result[f"displacement_bid_notional_{level}"],
            result[f"displacement_ask_notional_{level}"],
        )
        displacement_opposite = np.where(
            long_mask,
            result[f"displacement_ask_notional_{level}"],
            result[f"displacement_bid_notional_{level}"],
        )
        result[f"supportive_depth_log_change_{level}"] = np.log(
            displacement_support / probe_support,
        )
        result[f"opposing_depth_log_change_{level}"] = np.log(
            displacement_opposite / probe_opposite,
        )
        result[f"aligned_replenishment_{level}"] = (
            result[f"supportive_depth_log_change_{level}"]
            - result[f"opposing_depth_log_change_{level}"]
        )
    result["entry_dt"] = pd.to_datetime(result["entry_time_ns"], unit="ns", utc=True)
    return result


def _rules(frame: pd.DataFrame) -> dict[str, pd.Series]:
    flow = frame["trade_direction_flow_z"]
    return {
        "all": pd.Series(True, index=frame.index),
        "depth-support-z1": frame["displacement_aligned_depth_ratio_z_1"] >= 0.50,
        "depth-support-z2": frame["displacement_aligned_depth_ratio_z_2"] >= 0.50,
        "depth-replenishment-1": frame["aligned_replenishment_1"] >= 0.0,
        "depth-replenishment-2": frame["aligned_replenishment_2"] >= 0.0,
        "support-and-replenishment-1": (
            (frame["displacement_aligned_depth_ratio_z_1"] >= 0.0)
            & (frame["aligned_replenishment_1"] >= 0.0)
        ),
        "support-and-replenishment-2": (
            (frame["displacement_aligned_depth_ratio_z_2"] >= 0.0)
            & (frame["aligned_replenishment_2"] >= 0.0)
        ),
        "strong-flow": flow >= 1.70,
        "strong-flow-or-depth": (
            (flow >= 1.70)
            | (
                (frame["displacement_aligned_depth_ratio_z_1"] >= 0.50)
                & (frame["aligned_replenishment_1"] >= 0.0)
            )
        ),
        "strong-flow-and-depth": (
            (flow >= 1.25)
            & (frame["displacement_aligned_depth_ratio_z_1"] >= 0.0)
            & (frame["aligned_replenishment_1"] >= 0.0)
        ),
    }


def _score(frame: pd.DataFrame, role: str) -> list[dict[str, Any]]:
    source = frame.loc[frame["role"] == role].copy()
    rows: list[dict[str, Any]] = []
    for name, mask in _rules(source).items():
        selected = source.loc[mask]
        values = pd.to_numeric(selected["realized_r"], errors="coerce").dropna()
        gross_profit = float(values[values > 0.0].sum())
        gross_loss = abs(float(values[values < 0.0].sum()))
        quarters: list[dict[str, Any]] = []
        if role == "development" and not selected.empty:
            for quarter, group in selected.groupby(selected["entry_dt"].dt.quarter):
                quarters.append(
                    {
                        "quarter": int(quarter),
                        "trades": int(len(group)),
                        "sum_r": float(group["realized_r"].sum()),
                    },
                )
        rows.append(
            {
                "role": role,
                "rule": name,
                "trades": int(len(values)),
                "sum_r": float(values.sum()),
                "mean_r": float(values.mean()) if len(values) else None,
                "win_rate": float((values > 0.0).mean()) if len(values) else None,
                "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
                "quarters": quarters,
            },
        )
    return rows


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    combined_rows: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        raw_frame, _ = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache / "klines",
            warmup_minutes=max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180),
        )
        bars = to_auction_bars(raw_frame)
        trades, _, _ = simulate(
            variant=Variant("btc-240", ("BTCUSDT",), (candidate.range_minutes,)),
            bars_by_symbol={"BTCUSDT": bars},
            evaluation_start=start,
            evaluation_end=end,
            base_candidate=candidate,
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
            minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
            minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
            starting_nav=float(execution["starting_nav"]),
            risk_rates=(0.01,),
        )
        if trades.empty:
            continue
        context = _event_context(bars, candidate)
        trades = trades.merge(context, on="scenario_id", how="left", validate="one_to_one")
        days = _event_days(trades)
        records = _download_depth_days(
            days,
            cache_dir=args.cache / "depth",
            workers=args.workers,
        )
        manifest.extend(record.to_dict() for record in records)
        depth = _read_depth(records)
        joined = pd.concat(
            [
                trades.reset_index(drop=True),
                _asof(trades["probe_time_ns"], depth, "probe"),
                _asof(trades["displacement_time_ns"], depth, "displacement"),
                _asof(trades["entry_time_ns"], depth, "entry_depth"),
            ],
            axis=1,
        )
        joined.insert(0, "segment", label)
        joined.insert(1, "role", role)
        joined = _derive(joined)
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        joined.to_csv(destination / "trades_with_depth.csv", index=False)
        combined_rows.append(joined)

    combined = pd.concat(combined_rows, ignore_index=True)
    combined.to_csv(output / "combined_trades_with_depth.csv", index=False)
    scores = _score(combined, "development") + _score(combined, "quick")
    pd.DataFrame(scores).to_csv(output / "depth_rule_scores.csv", index=False)
    files = pd.DataFrame(manifest).drop_duplicates(["data_type", "symbol", "day"])
    _atomic_json(
        output / "depth_manifest.json",
        {"provider": "Binance Vision", "files": files.to_dict(orient="records")},
    )
    summary = {
        "trades": int(len(combined)),
        "development_trades": int((combined["role"] == "development").sum()),
        "quick_trades": int((combined["role"] == "quick").sum()),
        "rules": scores,
    }
    _atomic_json(output / "depth_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-depth")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-depth")
    parser.add_argument("--workers", type=int, default=24)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
