#!/usr/bin/env python3
"""Assemble monthly causal chunks and run one unbroken Nautilus account."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE19 = HERE.parent / "candidate-19"
CANDIDATE18 = HERE.parent / "candidate-18"
CANDIDATE17 = HERE.parent / "candidate-17"
CANDIDATE05 = HERE.parent / "candidate-05"

for path in (CANDIDATE16, HERE, CANDIDATE19, CANDIDATE18, CANDIDATE17, CANDIDATE05):
    sys.path.insert(0, str(path))

from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract
from positioning_contract import install as install_positioning_contract
from basis_contract import install as install_basis_contract
from book_depth_gap_contract import install as install_book_depth_gap_contract

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()
install_book_depth_gap_contract()

import backtest as candidate05_backtest
from nautilus_trader.config import ImportableStrategyConfig as NautilusImportableStrategyConfig
from smc_ict_4.manifest import write_json_atomic


class ContinuousError(RuntimeError):
    """Raised when a restarted or incomplete path could contaminate evidence."""


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _expected_minutes(start: date, end: date) -> int:
    return ((end - start).days + 1) * 1_440


def _load_chunks(
    *,
    input_root: Path,
    symbol: str,
    start: date,
    end: date,
    workspace: Path,
) -> tuple[pd.DataFrame, Path, dict[str, Any]]:
    manifest_paths = sorted(input_root.rglob("chunk_manifest.json"))
    if not manifest_paths:
        raise ContinuousError(f"no chunk manifests under {input_root}")

    records: list[tuple[dict[str, Any], Path]] = []
    for path in manifest_paths:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("symbol") != symbol:
            raise ContinuousError(f"unexpected symbol in {path}: {manifest.get('symbol')}")
        records.append((manifest, path.parent))
    records.sort(key=lambda item: item[0]["core_start"])

    cursor = start
    kline_frames: list[pd.DataFrame] = []
    feature_frames: list[pd.DataFrame] = []
    chunk_records: list[dict[str, Any]] = []
    for manifest, directory in records:
        core_start = date.fromisoformat(manifest["core_start"])
        core_end = date.fromisoformat(manifest["core_end"])
        if core_start != cursor:
            raise ContinuousError(
                f"chunk boundary is not continuous: expected {cursor}, got {core_start}",
            )
        if core_end < core_start:
            raise ContinuousError(f"invalid chunk end in {directory}")
        cursor = core_end + timedelta(days=1)

        kline_path = directory / "klines.csv.gz"
        feature_path = directory / "features.csv.gz"
        for name, path in (("klines.csv.gz", kline_path), ("features.csv.gz", feature_path)):
            expected_hash = manifest["files"][name]["sha256"]
            actual_hash = _sha256(path)
            if actual_hash != expected_hash:
                raise ContinuousError(
                    f"chunk hash mismatch for {path}: {actual_hash} != {expected_hash}",
                )

        klines = pd.read_csv(kline_path, compression="infer")
        klines["open_time_dt"] = pd.to_datetime(klines["open_time_dt"], utc=True, errors="raise")
        klines["close_time_dt"] = pd.to_datetime(klines["close_time_dt"], utc=True, errors="raise")
        features = pd.read_csv(feature_path, compression="infer")
        features["observed_time_ns"] = pd.to_numeric(
            features["observed_time_ns"],
            errors="raise",
        ).astype("int64")
        kline_frames.append(klines)
        feature_frames.append(features)
        chunk_records.append(
            {
                "core_start": manifest["core_start"],
                "core_end": manifest["core_end"],
                "rows": int(manifest["rows"]),
                "feature_sha256": manifest["files"]["features.csv.gz"]["sha256"],
                "kline_sha256": manifest["files"]["klines.csv.gz"]["sha256"],
            },
        )

    if cursor != end + timedelta(days=1):
        raise ContinuousError(
            f"chunk coverage ended at {cursor - timedelta(days=1)}, expected {end}",
        )

    klines = pd.concat(kline_frames, ignore_index=True)
    features = pd.concat(feature_frames, ignore_index=True)
    klines = klines.sort_values("close_time_dt").reset_index(drop=True)
    features = features.sort_values("observed_time_ns").reset_index(drop=True)

    expected_rows = _expected_minutes(start, end)
    if len(klines) != expected_rows or len(features) != expected_rows:
        raise ContinuousError(
            f"continuous rows mismatch: klines={len(klines)} features={len(features)} "
            f"expected={expected_rows}",
        )
    if klines["close_time_dt"].duplicated().any():
        raise ContinuousError("duplicate kline close timestamps after assembly")
    if features["observed_time_ns"].duplicated().any():
        raise ContinuousError("duplicate feature timestamps after assembly")
    if not klines["close_time_dt"].is_monotonic_increasing:
        raise ContinuousError("kline timestamps are not monotonic")
    if not features["observed_time_ns"].is_monotonic_increasing:
        raise ContinuousError("feature timestamps are not monotonic")

    expected_index = pd.date_range(
        pd.Timestamp(start, tz="UTC"),
        pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1),
        freq="1min",
    )
    actual_index = pd.DatetimeIndex(klines["close_time_dt"].dt.floor("min"))
    if not actual_index.equals(expected_index):
        missing = expected_index.difference(actual_index)[:10]
        raise ContinuousError(f"assembled kline grid has gaps: {list(map(str, missing))}")

    kline_ns = np.fromiter(
        (pd.Timestamp(value).value for value in klines["close_time_dt"]),
        dtype=np.int64,
        count=len(klines),
    )
    feature_ns = features["observed_time_ns"].to_numpy(dtype=np.int64, copy=False)
    if not np.array_equal(kline_ns, feature_ns):
        mismatch = np.flatnonzero(kline_ns != feature_ns)[:5]
        raise ContinuousError(f"feature/kline causal timestamps differ at {mismatch.tolist()}")

    workspace.mkdir(parents=True, exist_ok=True)
    feature_path = workspace / "features.csv.gz"
    kline_path = workspace / "klines.csv.gz"
    features.to_csv(feature_path, index=False, compression="gzip")
    klines.to_csv(kline_path, index=False, compression="gzip")

    input_manifest = {
        "schema_version": 1,
        "candidate": "candidate-29-continuous-replay",
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": (end - start).days + 1,
        "minute_rows": expected_rows,
        "chunk_count": len(chunk_records),
        "account_restarts": 0,
        "strategy_restarts": 0,
        "features_sha256": _sha256(feature_path),
        "klines_sha256": _sha256(kline_path),
        "chunks": chunk_records,
    }
    return klines, feature_path, input_manifest


def _strategy_config(
    *,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
) -> NautilusImportableStrategyConfig:
    del strategy_path, config_path
    return NautilusImportableStrategyConfig(
        strategy_path="long_strategy:Candidate29Strategy",
        config_path="long_strategy:Candidate29Config",
        config=config,
    )


def _rolling_growth(
    daily_returns: dict[str, float],
    *,
    starting_nav: float,
    window: int,
) -> dict[str, Any]:
    ordered = pd.Series(daily_returns, dtype=float).sort_index()
    nav = starting_nav * (1.0 + ordered).cumprod()
    prior = nav.shift(window)
    total = nav / prior - 1.0
    geometric = (nav / prior).pow(1.0 / window) - 1.0
    valid_total = total.dropna()
    valid_geo = geometric.dropna()
    if valid_total.empty:
        return {"window_days": window, "windows": 0}
    return {
        "window_days": window,
        "windows": int(len(valid_total)),
        "positive_window_share": float((valid_total > 0.0).mean()),
        "min_total_return": float(valid_total.min()),
        "median_total_return": float(valid_total.median()),
        "max_total_return": float(valid_total.max()),
        "min_geometric_daily_growth": float(valid_geo.min()),
        "median_geometric_daily_growth": float(valid_geo.median()),
        "max_geometric_daily_growth": float(valid_geo.max()),
        "worst_window_end": str(valid_total.idxmin()),
        "best_window_end": str(valid_total.idxmax()),
    }


def _year_returns(
    daily_returns: dict[str, float],
    *,
    starting_nav: float,
) -> dict[str, float]:
    ordered = pd.Series(daily_returns, dtype=float).sort_index()
    dates = pd.to_datetime(ordered.index, utc=True)
    nav = starting_nav * (1.0 + ordered).cumprod()
    result: dict[str, float] = {}
    previous = starting_nav
    for year in sorted(set(dates.year)):
        values = nav[dates.year == year]
        if values.empty:
            continue
        ending = float(values.iloc[-1])
        result[str(year)] = ending / previous - 1.0
        previous = ending
    return result


def _duration_scaled_gate(metrics: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    days = int(metrics["calendar_days"])
    weeks = days / 7.0
    min_trades = math.ceil(7 * weeks)
    min_wins = math.ceil(4 * weeks)
    min_active_days = math.ceil(4 * weeks)
    checks = {
        "geometric_daily_growth": float(metrics["geometric_daily_growth"]) >= 0.01,
        "trades": int(metrics["trades"]) >= min_trades,
        "wins": int(metrics["wins"]) >= min_wins,
        "win_rate": float(metrics["win_rate"]) >= 0.40,
        "active_days": int(metrics["active_days"]) >= min_active_days,
        "max_drawdown": float(metrics["max_drawdown"]) <= 0.20,
        "largest_winner_share": float(metrics["largest_winner_share"]) <= 0.55,
        "positive_nav": float(metrics["ending_nav"]) > 0.0 and float(metrics["min_equity"]) > 0.0,
        "no_liquidation": int(metrics["liquidations"]) == 0,
        "no_order_rejections": int(
            metrics["strategy_diagnostics"].get("order_rejections", 0),
        ) == 0,
        "single_entry_intent": int(
            metrics["strategy_diagnostics"].get("max_simultaneous_entry_intents", 0),
        ) <= 1,
        "single_position": int(
            metrics["strategy_diagnostics"].get("max_open_positions_observed", 0),
        ) <= 1,
        "continuous_account": True,
        "continuous_strategy_process": True,
    }
    thresholds = {
        "calendar_days": days,
        "equivalent_weeks": weeks,
        "min_geometric_daily_growth": 0.01,
        "min_trades": min_trades,
        "min_wins": min_wins,
        "min_win_rate": 0.40,
        "min_active_days": min_active_days,
        "max_drawdown": 0.20,
        "max_largest_winner_share": 0.55,
    }
    return checks, thresholds


def run(
    *,
    input_root: Path,
    output: Path,
    workspace: Path,
    cache: Path,
    symbol: str,
    start: date,
    end: date,
    config_path: Path,
) -> dict[str, Any]:
    output = output.resolve()
    workspace = workspace.resolve()
    cache = cache.resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    klines, feature_path, input_manifest = _load_chunks(
        input_root=input_root.resolve(),
        symbol=symbol,
        start=start,
        end=end,
        workspace=workspace,
    )
    manifest_path = cache / "continuous_input_manifest.json"
    write_json_atomic(manifest_path, input_manifest)

    def prebuilt_load_range(
        *,
        symbol: str,
        start: date,
        end: date,
        cache: Path,
        output: Path,
    ):
        del cache, output
        if symbol != input_manifest["symbol"]:
            raise ContinuousError(f"requested symbol changed: {symbol}")
        if start.isoformat() != input_manifest["start"] or end.isoformat() != input_manifest["end"]:
            raise ContinuousError(f"requested range changed: {start} through {end}")
        return klines, feature_path, [manifest_path], []

    candidate05_backtest.load_range = prebuilt_load_range
    candidate05_backtest.ImportableStrategyConfig = _strategy_config

    legacy = candidate05_backtest.run_backtest(
        config_path=config_path.resolve(),
        build_start=start,
        build_end=end,
        evaluation_start=start,
        evaluation_end=end,
        cache=cache,
        output=output,
    )
    legacy_checks = dict(legacy["gate_checks"])
    legacy_pass = bool(legacy["gate_pass"])
    long_checks, thresholds = _duration_scaled_gate(legacy)

    legacy.update(
        {
            "candidate": "candidate-29-frozen-candidate19-continuous",
            "validation_mode": "single-unbroken-2024-2026-account",
            "continuous_account": True,
            "continuous_strategy_process": True,
            "account_restarts": 0,
            "strategy_restarts": 0,
            "input_chunk_count": int(input_manifest["chunk_count"]),
            "input_manifest_sha256": _sha256(manifest_path),
            "legacy_short_horizon_gate_checks": legacy_checks,
            "legacy_short_horizon_gate_pass": legacy_pass,
            "duration_scaled_thresholds": thresholds,
            "gate_checks": long_checks,
            "gate_pass": all(long_checks.values()),
            "rolling_windows": {
                str(window): _rolling_growth(
                    legacy["daily_returns"],
                    starting_nav=float(legacy["starting_nav"]),
                    window=window,
                )
                for window in (30, 90, 180, 365)
            },
            "calendar_year_returns": _year_returns(
                legacy["daily_returns"],
                starting_nav=float(legacy["starting_nav"]),
            ),
        },
    )
    write_json_atomic(output / "metrics.json", legacy)
    write_json_atomic(output / "continuous_input_manifest.json", input_manifest)
    return legacy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=CANDIDATE19 / "config.json",
    )
    args = parser.parse_args()
    result = run(
        input_root=args.input_root,
        output=args.output,
        workspace=args.workspace,
        cache=args.cache,
        symbol=args.symbol,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        config_path=args.config,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
