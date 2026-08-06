#!/usr/bin/env python3
"""Run BTC, ETH, SOL and XRP in one NautilusTrader account and BacktestNode.

This module is an orchestration and evidence layer only.  NautilusTrader owns
market-event replay, order matching, fills, contingent orders, fees, positions,
margin, liquidation, portfolio accounting and the shared account NAV.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
import json
import math
import os
from pathlib import Path
import shutil
import traceback
from typing import Any

import numpy as np
import pandas as pd

from nautilus_trader.backtest.config import BacktestDataConfig
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.config import BacktestRunConfig
from nautilus_trader.backtest.config import BacktestVenueConfig
from nautilus_trader.backtest.config import ImportableFeeModelConfig
from nautilus_trader.backtest.config import ImportableFillModelConfig
from nautilus_trader.backtest.config import ImportableLatencyModelConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import BarDataWrangler

from backtest import make_instrument
from features import load_range
from features import sha256_file
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
from global_entry_slot_v4 import reset_final_shared_account_entry_coordinator
from shared_account_strategy_variants_v2 import final_shared_strategy_path


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
CONFIG_FILES = {
    "BTCUSDT": "config.json",
    "ETHUSDT": "config_eth.json",
    "SOLUSDT": "config_sol.json",
    "XRPUSDT": "config_xrp.json",
}


class SharedAccountError(RuntimeError):
    """Raised when shared-account evidence cannot be trusted."""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    method = getattr(value, "as_double", None)
    if callable(method):
        number = float(method())
        return number if math.isfinite(number) else None
    text = str(value).strip().split()[0].replace("_", "").replace(",", "")
    try:
        number = float(text)
    except (ValueError, IndexError):
        return None
    return number if math.isfinite(number) else None


def report_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if not isinstance(result.index, pd.RangeIndex) or result.index.name is not None:
        result = result.reset_index()
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [
            "_".join(str(part) for part in column if str(part) != "")
            for column in result.columns
        ]
    else:
        result.columns = [str(column) for column in result.columns]
    return result


def find_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    normalized = {
        str(column).lower().replace(" ", "_").replace("-", "_"): str(column)
        for column in frame.columns
    }
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    for name, original in normalized.items():
        if any(candidate in name for candidate in candidates):
            return original
    return None


def summary_int(summary: dict[str, Any], key: str) -> int | None:
    value = summary.get(key)
    number = as_number(value)
    return None if number is None else int(number)


def load_validated_winner(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise SharedAccountError(f"validated winner evidence not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("classification") != "VALIDATED_BTC_WINNER_RESOLVED":
        raise SharedAccountError(
            "shared-account validation requires VALIDATED_BTC_WINNER_RESOLVED",
        )
    winner = payload.get("winner")
    if not isinstance(winner, str) or not winner:
        raise SharedAccountError("validated winner evidence contains no winner")
    for symbol in PROJECT_SYMBOLS:
        final_shared_strategy_path(winner, symbol)
    return payload, winner


def load_configs(root: Path) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for symbol, filename in CONFIG_FILES.items():
        path = root / filename
        if not path.exists():
            raise SharedAccountError(f"missing symbol config: {path}")
        config = json.loads(path.read_text(encoding="utf-8"))
        if config.get("symbol") != symbol:
            raise SharedAccountError(
                f"symbol config mismatch for {symbol}: {config.get('symbol')}",
            )
        if abs(float(config.get("risk_fraction", -1.0)) - 0.03) > 1e-12:
            raise SharedAccountError(f"{symbol} risk fraction must remain exactly 3%")
        configs[symbol] = config

    reference = configs["BTCUSDT"]
    invariant_keys = (
        "starting_nav",
        "risk_fraction",
        "all_in_cost_bps_each_side",
        "adverse_slippage_bps_each_side",
        "venue_leverage",
        "maintenance_margin_rate",
        "execution_seed",
    )
    for symbol, config in configs.items():
        for key in invariant_keys:
            if config.get(key) != reference.get(key):
                raise SharedAccountError(
                    f"shared-account invariant differs for {symbol}: {key}",
                )
        if config.get("strategy") != reference.get("strategy"):
            raise SharedAccountError(
                f"strategy parameters must be identical across project symbols: {symbol}",
            )
    return configs


def instrument_id_of(instrument: Any) -> InstrumentId:
    value = getattr(instrument, "id", None)
    if value is None:
        value = getattr(instrument, "instrument_id", None)
    if isinstance(value, InstrumentId):
        return value
    if value is None:
        raise SharedAccountError(f"instrument has no identifier: {instrument}")
    return InstrumentId.from_str(str(value))


def bar_type_of(instrument_id: InstrumentId) -> BarType:
    return BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")


def writable_bar_frame(klines: pd.DataFrame) -> pd.DataFrame:
    source = klines.set_index("close_time_dt")[["open", "high", "low", "close", "volume"]]
    result = source.astype(float).copy(deep=True)
    for column in result.columns:
        result[column] = np.ascontiguousarray(result[column].to_numpy(dtype=float, copy=True))
    return result


def prepare_shared_catalog(
    *,
    configs: dict[str, dict[str, Any]],
    build_start: date,
    build_end: date,
    cache_root: Path,
    output: Path,
    catalog_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, InstrumentId],
    dict[str, BarType],
    dict[str, Path],
    dict[str, int],
]:
    if catalog_path.exists():
        shutil.rmtree(catalog_path)
    catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(catalog_path)

    instruments: dict[str, Any] = {}
    instrument_ids: dict[str, InstrumentId] = {}
    bar_types: dict[str, BarType] = {}
    feature_paths: dict[str, Path] = {}
    bar_counts: dict[str, int] = {}
    manifest_symbols: dict[str, Any] = {}

    for symbol in PROJECT_SYMBOLS:
        symbol_output = output / "symbols" / symbol
        symbol_output.mkdir(parents=True, exist_ok=True)
        klines, feature_path, raw_files, _ = load_range(
            symbol=symbol,
            start=build_start,
            end=build_end,
            cache=(cache_root / symbol).resolve(),
            output=symbol_output,
        )
        instrument = make_instrument(configs[symbol])
        instrument_id = instrument_id_of(instrument)
        bar_type = bar_type_of(instrument_id)
        bars = BarDataWrangler(bar_type, instrument).process(writable_bar_frame(klines))
        if not bars:
            raise SharedAccountError(f"BarDataWrangler produced no bars for {symbol}")

        instruments[symbol] = instrument
        instrument_ids[symbol] = instrument_id
        bar_types[symbol] = bar_type
        feature_paths[symbol] = Path(feature_path).resolve()
        bar_counts[symbol] = len(bars)
        manifest_symbols[symbol] = {
            "instrument_id": str(instrument_id),
            "bar_type": str(bar_type),
            "bars": len(bars),
            "feature_path": str(feature_paths[symbol]),
            "feature_sha256": sha256_file(feature_paths[symbol]),
            "raw_files": [
                {
                    "path": str(Path(path).resolve()),
                    "size": Path(path).stat().st_size,
                    "sha256": sha256_file(Path(path)),
                }
                for path in raw_files
            ],
        }

        catalog.write_data([instrument])
        catalog.write_data(bars)

    manifest = {
        "schema": "candidate-05-shared-account-data-manifest-v1",
        "dataset": "binance-usdm-1m-aggtrades-bookdepth",
        "build_start": str(build_start),
        "build_end": str(build_end),
        "timestamp_semantics": "completed Binance one-minute bar close_time",
        "feature_observation_contract": "observed_time_ns <= strategy bar ts_event",
        "symbols": manifest_symbols,
    }
    write_json(output / "data_manifest.json", manifest)
    return instruments, instrument_ids, bar_types, feature_paths, bar_counts


def importable_strategy_configs(
    *,
    winner: str,
    configs: dict[str, dict[str, Any]],
    instrument_ids: dict[str, InstrumentId],
    bar_types: dict[str, BarType],
    feature_paths: dict[str, Path],
    evaluation_start: date,
    evaluation_end: date,
    output: Path,
) -> list[ImportableStrategyConfig]:
    start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    end_ns = int(
        (
            pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
            - pd.Timedelta(nanoseconds=1)
        ).value,
    )
    strategies: list[ImportableStrategyConfig] = []
    for symbol in PROJECT_SYMBOLS:
        config = configs[symbol]
        symbol_output = (output / "symbols" / symbol).resolve()
        values = dict(config["strategy"])
        values.update(
            {
                "instrument_id": str(instrument_ids[symbol]),
                "bar_type": str(bar_types[symbol]),
                "output_dir": str(symbol_output),
                "features_path": str(feature_paths[symbol]),
                "evaluation_start_ns": start_ns,
                "evaluation_end_ns": end_ns,
                "starting_nav": float(config["starting_nav"]),
                "risk_fraction": float(config["risk_fraction"]),
                "all_in_cost_bps_each_side": float(config["all_in_cost_bps_each_side"]),
                "adverse_slippage_bps_each_side": float(config["adverse_slippage_bps_each_side"]),
            },
        )
        strategies.append(
            ImportableStrategyConfig(
                strategy_path=final_shared_strategy_path(winner, symbol),
                config_path="strategy_base:LiquidityResponseConfig",
                config=values,
            ),
        )
    return strategies


def normalize_equity_files(
    *,
    output: Path,
    evaluation_start: date,
    evaluation_end: date,
    starting_nav: float,
    ending_nav: float,
) -> tuple[pd.DataFrame, dict[str, float], float, float]:
    frames: list[pd.DataFrame] = []
    symbol_order = {symbol: index for index, symbol in enumerate(PROJECT_SYMBOLS)}
    for symbol in PROJECT_SYMBOLS:
        path = output / "symbols" / symbol / "equity.csv"
        if not path.exists() or path.stat().st_size == 0:
            raise SharedAccountError(f"missing strategy equity observations: {symbol}")
        frame = pd.read_csv(path)
        if not {"ts_event", "equity"}.issubset(frame.columns):
            raise SharedAccountError(f"invalid equity schema for {symbol}: {list(frame.columns)}")
        frame = frame[["ts_event", "equity"]].copy()
        frame["ts_event"] = pd.to_numeric(frame["ts_event"], errors="raise").astype("int64")
        frame["equity"] = pd.to_numeric(frame["equity"], errors="raise").astype(float)
        frame["symbol"] = symbol
        frame["symbol_order"] = symbol_order[symbol]
        frame["row_order"] = np.arange(len(frame), dtype=np.int64)
        frame["time"] = pd.to_datetime(frame["ts_event"], unit="ns", utc=True)
        frames.append(frame)

    equity = pd.concat(frames, ignore_index=True).sort_values(
        ["ts_event", "symbol_order", "row_order"],
        kind="stable",
    )
    start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    final_boundary = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
    selected = equity[(equity["time"] >= start_ts) & (equity["time"] <= final_boundary)].copy()
    if selected.empty:
        raise SharedAccountError("no shared equity observations in evaluation range")

    cursor = float(starting_nav)
    daily_returns: dict[str, float] = {}
    for offset in range((evaluation_end - evaluation_start).days + 1):
        day = evaluation_start + timedelta(days=offset)
        boundary = pd.Timestamp(day + timedelta(days=1), tz="UTC")
        if day == evaluation_end:
            close = float(ending_nav)
        else:
            after = selected[selected["time"] >= boundary]
            if not after.empty:
                close = float(after.iloc[0]["equity"])
            else:
                before = selected[selected["time"] < boundary]
                close = cursor if before.empty else float(before.iloc[-1]["equity"])
        daily_returns[str(day)] = close / cursor - 1.0
        cursor = close

    trajectory = pd.concat(
        [
            pd.Series([float(starting_nav)], dtype=float),
            selected["equity"].astype(float).reset_index(drop=True),
            pd.Series([float(ending_nav)], dtype=float),
        ],
        ignore_index=True,
    )
    peaks = trajectory.cummax()
    max_drawdown = float((1.0 - trajectory / peaks).max())
    min_equity = float(trajectory.min())
    selected.to_csv(output / "shared_equity_observations.csv", index=False)
    write_json(output / "daily_returns.json", daily_returns)
    return selected, daily_returns, max_drawdown, min_equity


def position_pnls(positions: pd.DataFrame) -> list[float]:
    if positions.empty:
        return []
    column = find_column(positions, ("realized_pnl", "realized_return", "pnl"))
    if column is None:
        return []
    values = [as_number(value) for value in positions[column].tolist()]
    return [value for value in values if value is not None]


def closed_scenario_records(output: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for symbol in PROJECT_SYMBOLS:
        path = output / "symbols" / symbol / "closed_scenarios.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SharedAccountError(f"closed scenario file is not a list: {symbol}")
        for item in payload:
            record = dict(item)
            record["symbol"] = symbol
            record["realized_pnl_number"] = as_number(record.get("realized_pnl"))
            records.append(record)
    records.sort(key=lambda item: (int(item.get("ts_event", 0)), str(item.get("symbol", ""))))
    write_json(output / "closed_scenarios_all.json", records)
    return records


def aggregate_scenarios(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    by_branch: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for record in records:
        pnl = record.get("realized_pnl_number")
        if pnl is None:
            continue
        branch = str(record.get("branch", "UNKNOWN"))
        symbol = str(record.get("symbol", "UNKNOWN"))
        for key, bucket_name in ((branch, "branch"), (symbol, "symbol")):
            target = by_branch if bucket_name == "branch" else by_symbol
            bucket = target.setdefault(
                key,
                {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0},
            )
            bucket["trades"] += 1
            bucket["wins"] += int(pnl > 0.0)
            bucket["losses"] += int(pnl < 0.0)
            bucket["net_pnl"] += float(pnl)
    return by_branch, by_symbol


def count_liquidations(output: Path) -> int:
    total = 0
    for symbol in PROJECT_SYMBOLS:
        for filename in ("scenario_events.jsonl", "closed_scenarios.json"):
            path = output / "symbols" / symbol / filename
            if not path.exists():
                continue
            total += path.read_text(encoding="utf-8", errors="replace").upper().count("LIQUIDAT")
    return total


def load_diagnostics(output: Path) -> dict[str, dict[str, Any]]:
    diagnostics: dict[str, dict[str, Any]] = {}
    for symbol in PROJECT_SYMBOLS:
        path = output / "symbols" / symbol / "strategy_diagnostics.json"
        if not path.exists():
            raise SharedAccountError(f"missing strategy diagnostics: {symbol}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise SharedAccountError(f"invalid strategy diagnostics: {symbol}")
        diagnostics[symbol] = value
    return diagnostics


def build_shared_metrics(
    *,
    result: Any,
    positions: pd.DataFrame,
    output: Path,
    configs: dict[str, dict[str, Any]],
    validated_winner: dict[str, Any],
    winner: str,
    evaluation_start: date,
    evaluation_end: date,
    bar_counts: dict[str, int],
) -> dict[str, Any]:
    starting_nav = float(configs["BTCUSDT"]["starting_nav"])
    result_summary = dict(result.summary)
    ending_nav = as_number(result_summary.get("account.BINANCE.balance.USDT.total"))
    if ending_nav is None:
        ending_nav = as_number(result_summary.get("account.BINANCE.balance.USDT.free"))
    if ending_nav is None:
        raise SharedAccountError("Nautilus result summary contains no final shared USDT NAV")

    _, daily_returns, max_drawdown, min_equity = normalize_equity_files(
        output=output,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        starting_nav=starting_nav,
        ending_nav=ending_nav,
    )
    days = (evaluation_end - evaluation_start).days + 1
    geometric_daily = (ending_nav / starting_nav) ** (1.0 / days) - 1.0 if ending_nav > 0.0 else -1.0

    pnls = position_pnls(positions)
    records = closed_scenario_records(output)
    if not pnls and records:
        pnls = [
            float(record["realized_pnl_number"])
            for record in records
            if record.get("realized_pnl_number") is not None
        ]
    trades = len(pnls)
    wins = sum(value > 0.0 for value in pnls)
    losses = sum(value < 0.0 for value in pnls)
    gross_profit = sum(value for value in pnls if value > 0.0)
    gross_loss = -sum(value for value in pnls if value < 0.0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0.0 else (math.inf if gross_profit > 0.0 else 0.0)
    largest_winner_share = (
        max((value for value in pnls if value > 0.0), default=0.0) / gross_profit
        if gross_profit > 0.0
        else 1.0
    )
    scenario_metrics, symbol_metrics = aggregate_scenarios(records)
    diagnostics = load_diagnostics(output)
    slot_events = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.events()
    slot_audit = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.audit()
    write_json(output / "global_slot_events.json", slot_events)
    write_json(output / "global_slot_audit.json", slot_audit)

    order_rejections = sum(int(item.get("order_rejections", 0) or 0) for item in diagnostics.values())
    order_denials = sum(int(item.get("order_denials", 0) or 0) for item in diagnostics.values())
    local_max_intents = max(
        (int(item.get("max_simultaneous_entry_intents", 0) or 0) for item in diagnostics.values()),
        default=0,
    )
    local_max_positions = max(
        (int(item.get("max_open_positions_observed", 0) or 0) for item in diagnostics.values()),
        default=0,
    )
    liquidations = count_liquidations(output)
    active_days = sum(abs(value) > 1e-12 for value in daily_returns.values())
    open_orders = summary_int(result_summary, "orders.open")
    inflight_orders = summary_int(result_summary, "orders.inflight")
    open_positions = summary_int(result_summary, "positions.open")
    parsed_records = sum(record.get("realized_pnl_number") is not None for record in records)

    integrity_checks = {
        "engine_is_nautilus": True,
        "one_shared_venue_account": str(result_summary.get("venues.total")) in {"1", "1.0"},
        "four_project_symbols_loaded": set(bar_counts) == set(PROJECT_SYMBOLS),
        "all_symbol_bars_positive": all(count > 0 for count in bar_counts.values()),
        "risk_fraction_exactly_three_percent": all(
            abs(float(config["risk_fraction"]) - 0.03) <= 1e-12
            for config in configs.values()
        ),
        "positive_nav": ending_nav > 0.0 and min_equity > 0.0,
        "no_liquidation": liquidations == 0,
        "no_order_rejections": order_rejections == 0,
        "no_order_denials": order_denials == 0,
        "local_single_entry_intent": local_max_intents <= 1,
        "local_single_position": local_max_positions <= 1,
        "global_slot_audit": bool(slot_audit.get("audit_pass")),
        "global_unfilled_entry_intents": int(slot_audit.get("max_unfilled_entry_intents_replayed", 99)) <= 1,
        "global_open_positions": int(slot_audit.get("max_open_positions_replayed", 99)) <= 1,
        "global_entry_plus_positions": int(slot_audit.get("max_entry_intents_plus_positions_replayed", 99)) <= 1,
        "global_slot_idle_at_end": bool(slot_audit.get("idle_at_end")),
        "no_open_orders_at_end": open_orders in {None, 0},
        "no_inflight_orders_at_end": inflight_orders in {None, 0},
        "no_open_positions_at_end": open_positions in {None, 0},
        "nautilus_positions_match_pnl_rows": int(result.total_positions) == trades,
        "scenario_records_match_trades": parsed_records == trades,
        "four_strategy_diagnostics": set(diagnostics) == set(PROJECT_SYMBOLS),
    }

    return {
        "schema": "candidate-05-one-account-four-symbol-metrics-v1",
        "candidate": "candidate-05-shared-account",
        "engine": "NautilusTrader BacktestNode",
        "venue": "BINANCE",
        "account_type": "MARGIN",
        "symbols": list(PROJECT_SYMBOLS),
        "validated_winner": validated_winner,
        "strategy": winner,
        "evaluation_start": str(evaluation_start),
        "evaluation_end": str(evaluation_end),
        "calendar_days": days,
        "starting_nav": starting_nav,
        "ending_nav": ending_nav,
        "total_return": ending_nav / starting_nav - 1.0,
        "geometric_daily_growth": geometric_daily,
        "max_drawdown": max_drawdown,
        "min_equity": min_equity,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / trades if trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": None if math.isinf(profit_factor) else profit_factor,
        "expectancy_usdt": sum(pnls) / trades if trades else 0.0,
        "active_days": active_days,
        "largest_winner_share": largest_winner_share,
        "daily_returns": daily_returns,
        "scenario_metrics": scenario_metrics,
        "symbol_metrics": symbol_metrics,
        "strategy_diagnostics": diagnostics,
        "global_slot_audit": slot_audit,
        "liquidations": liquidations,
        "bar_counts": bar_counts,
        "nautilus_result": {
            "run_id": result.run_id,
            "iterations": result.iterations,
            "total_events": result.total_events,
            "total_orders": result.total_orders,
            "total_positions": result.total_positions,
            "summary": result_summary,
            "stats_pnls": result.stats_pnls,
            "stats_returns": result.stats_returns,
        },
        "integrity_checks": integrity_checks,
        "integrity_pass": all(integrity_checks.values()),
    }


def run_shared_account(
    *,
    winner_evidence_path: Path,
    build_start: date,
    build_end: date,
    evaluation_start: date,
    evaluation_end: date,
    cache_root: Path,
    output: Path,
) -> dict[str, Any]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not build_start <= evaluation_start <= evaluation_end <= build_end:
        raise SharedAccountError("evaluation must be contained in build range")

    validated_winner, winner = load_validated_winner(winner_evidence_path.resolve())
    root = Path(__file__).resolve().parent
    configs = load_configs(root)
    starting_nav = float(configs["BTCUSDT"]["starting_nav"])
    catalog_path = output / "catalog"
    reset_final_shared_account_entry_coordinator()

    node: BacktestNode | None = None
    try:
        instruments, instrument_ids, bar_types, feature_paths, bar_counts = prepare_shared_catalog(
            configs=configs,
            build_start=build_start,
            build_end=build_end,
            cache_root=cache_root.resolve(),
            output=output,
            catalog_path=catalog_path,
        )
        strategies = importable_strategy_configs(
            winner=winner,
            configs=configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            feature_paths=feature_paths,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            output=output,
        )

        reference = configs["BTCUSDT"]
        fill_model = ImportableFillModelConfig(
            fill_model_path="nautilus_trader.backtest.models:FillModel",
            config_path="nautilus_trader.backtest.config:FillModelConfig",
            config={
                "prob_fill_on_limit": 1.0,
                "prob_slippage": 1.0,
                "random_seed": int(reference["execution_seed"]),
            },
        )
        latency_model = ImportableLatencyModelConfig(
            latency_model_path="nautilus_trader.backtest.models:LatencyModel",
            config_path="nautilus_trader.backtest.config:LatencyModelConfig",
            config={
                "base_latency_nanos": 100_000_000,
                "insert_latency_nanos": 150_000_000,
                "update_latency_nanos": 100_000_000,
                "cancel_latency_nanos": 100_000_000,
            },
        )
        fee_model = ImportableFeeModelConfig(
            fee_model_path="nautilus_trader.backtest.models:MakerTakerFeeModel",
            config_path="nautilus_trader.backtest.config:MakerTakerFeeModelConfig",
            config={},
        )
        venue = BacktestVenueConfig(
            name="BINANCE",
            oms_type="NETTING",
            account_type="MARGIN",
            base_currency="USDT",
            starting_balances=[f"{starting_nav:.2f} USDT"],
            default_leverage=float(reference["venue_leverage"]),
            book_type="L1_MBP",
            fill_model=fill_model,
            latency_model=latency_model,
            fee_model=fee_model,
            use_position_ids=True,
            use_reduce_only=True,
            support_contingent_orders=True,
            bar_execution=True,
            bar_adaptive_high_low_ordering=True,
            trade_execution=False,
            liquidation_enabled=True,
            liquidation_trigger_ratio=1.0,
        )
        start_time = pd.Timestamp(build_start, tz="UTC").isoformat()
        end_time = (
            pd.Timestamp(build_end + timedelta(days=1), tz="UTC")
            - pd.Timedelta(nanoseconds=1)
        ).isoformat()
        data = [
            BacktestDataConfig(
                catalog_path=str(catalog_path),
                data_cls=Bar,
                instrument_id=instrument_ids[symbol],
                bar_spec="1-MINUTE-LAST",
                start_time=start_time,
                end_time=end_time,
            )
            for symbol in PROJECT_SYMBOLS
        ]
        engine = BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
            strategies=strategies,
            run_analysis=True,
        )
        run_config = BacktestRunConfig(
            engine=engine,
            venues=[venue],
            data=data,
            raise_exception=True,
            dispose_on_completion=False,
            start=start_time,
            end=end_time,
        )
        node = BacktestNode(configs=[run_config])
        results = node.run()
        if len(results) != 1:
            raise SharedAccountError(f"expected one shared Nautilus result, got {len(results)}")
        result = results[0]
        engines = node.get_engines()
        if len(engines) != 1:
            raise SharedAccountError(f"expected one shared Nautilus engine, got {len(engines)}")
        nt_engine = engines[0]
        orders = report_frame(nt_engine.trader.generate_order_fills_report())
        positions = report_frame(nt_engine.trader.generate_positions_report())
        account = report_frame(nt_engine.trader.generate_account_report(Venue("BINANCE")))
        orders.to_csv(output / "orders.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)

        metrics = build_shared_metrics(
            result=result,
            positions=positions,
            output=output,
            configs=configs,
            validated_winner=validated_winner,
            winner=winner,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            bar_counts=bar_counts,
        )
        write_json(output / "metrics.json", metrics)
        write_json(
            output / "run.json",
            {
                "schema": "candidate-05-shared-account-run-v1",
                "run_id": f"candidate-05-shared-{evaluation_start}-{evaluation_end}",
                "source_commit": os.environ.get("GITHUB_SHA"),
                "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
                "engine": "NautilusTrader BacktestNode",
                "one_account": True,
                "one_venue": True,
                "symbols": list(PROJECT_SYMBOLS),
                "strategy": winner,
                "build_start": str(build_start),
                "build_end": str(build_end),
                "evaluation_start": str(evaluation_start),
                "evaluation_end": str(evaluation_end),
                "risk_fraction": 0.03,
                "global_constraint": "unfilled new-entry intents plus open positions <= 1",
                "data_manifest": str(output / "data_manifest.json"),
            },
        )
        return metrics
    except Exception:
        (output / "errors.log").write_text(traceback.format_exc(), encoding="utf-8")
        write_json(
            output / "failure.json",
            {
                "schema": "candidate-05-shared-account-failure-v1",
                "source_commit": os.environ.get("GITHUB_SHA"),
                "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
                "error": traceback.format_exc(),
                "global_slot_events": FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.events(),
                "global_slot_audit": FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.audit(),
            },
        )
        raise
    finally:
        if node is not None:
            node.dispose()
        if catalog_path.exists():
            shutil.rmtree(catalog_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--winner-evidence", type=Path, required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = run_shared_account(
        winner_evidence_path=args.winner_evidence,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache_root=args.cache,
        output=args.output,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
