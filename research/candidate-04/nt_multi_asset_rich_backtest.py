#!/usr/bin/env python3
"""Run four causal signal streams in one NautilusTrader account.

This script is a NautilusTrader configuration and evidence exporter.  It does
not implement matching, fills, fees, positions, margin, liquidation, PnL or NAV.
Four instrument-specific strategies share one portfolio-wide entry coordinator,
so pending new entries plus open positions can never exceed one.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import inspect
import json
import math
from pathlib import Path
import shutil
from typing import Any
import urllib.request

import pandas as pd
from pandas.errors import EmptyDataError

# Importing the exact-target runner installs the validated exact causal target
# risk-sizing relation on LiquidityTransitionStrategy without executing main().
import nt_backtest_v31_exact_causal_target  # noqa: F401
import nt_backtest as single_base
from nt_global_rich_signal_strategy import GlobalRichSignalConfig
from nt_global_rich_signal_strategy import coordinator_snapshot
from nt_global_rich_signal_strategy import reset_global_coordinator

from nautilus_trader.backtest.config import BacktestDataConfig
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.config import BacktestRunConfig
from nautilus_trader.backtest.config import BacktestVenueConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, ETH, SOL, XRP, USDT
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import BarDataWrangler

from smc_ict_4.manifest import build_data_manifest
from smc_ict_4.manifest import write_data_manifest
from smc_ict_4.manifest import write_json_atomic


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BASE_URL = "https://data.binance.vision/data/futures/um/daily/klines"
KLINE_COLUMNS = single_base.KLINE_COLUMNS


class MultiAssetRunnerError(RuntimeError):
    pass


INSTRUMENT_SPECS: dict[str, dict[str, Any]] = {
    "BTCUSDT": {
        "base_currency": BTC,
        "price_precision": 1,
        "price_increment": "0.1",
        "size_precision": 3,
        "size_increment": "0.001",
        "min_quantity": "0.001",
    },
    "ETHUSDT": {
        "base_currency": ETH,
        "price_precision": 2,
        "price_increment": "0.01",
        "size_precision": 3,
        "size_increment": "0.001",
        "min_quantity": "0.001",
    },
    "SOLUSDT": {
        "base_currency": SOL,
        "price_precision": 3,
        "price_increment": "0.001",
        "size_precision": 2,
        "size_increment": "0.01",
        "min_quantity": "0.01",
    },
    "XRPUSDT": {
        "base_currency": XRP,
        "price_precision": 4,
        "price_increment": "0.0001",
        "size_precision": 1,
        "size_increment": "0.1",
        "min_quantity": "0.1",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def download_checked(symbol: str, day: date, cache: Path) -> tuple[Path, Path]:
    cache.mkdir(parents=True, exist_ok=True)
    name = f"{symbol}-1m-{day.isoformat()}.zip"
    url = f"{BASE_URL}/{symbol}/1m/{name}"
    target = cache / name
    checksum = cache / f"{name}.CHECKSUM"
    if not target.exists():
        urllib.request.urlretrieve(url, target)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(target)
    if actual != expected:
        raise MultiAssetRunnerError(
            f"checksum mismatch for {target}: {actual} != {expected}"
        )
    return target, checksum


def load_symbol(
    symbol: str,
    build_start: date,
    build_end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[Path]]:
    frames: list[pd.DataFrame] = []
    evidence: list[Path] = []
    cursor = build_start
    symbol_cache = cache / symbol
    while cursor <= build_end:
        archive, checksum = download_checked(symbol, cursor, symbol_cache)
        frames.append(single_base.read_daily_kline(archive))
        evidence.extend((archive, checksum))
        cursor += timedelta(days=1)
    frame = pd.concat(frames).sort_index()
    if frame.index.has_duplicates:
        raise MultiAssetRunnerError(f"duplicate timestamps for {symbol}")
    expected_days = (build_end - build_start).days + 1
    if len(frame) < expected_days * 1_430:
        raise MultiAssetRunnerError(
            f"incomplete minute data for {symbol}: {len(frame)} rows"
        )
    return frame, evidence


def instrument_id(symbol: str) -> InstrumentId:
    return InstrumentId.from_str(f"{symbol}-PERP.BINANCE")


def bar_type(symbol: str) -> BarType:
    return BarType.from_str(
        f"{symbol}-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    )


def make_instrument(
    symbol: str,
    all_in_cost_bps_each_side: float,
) -> CryptoPerpetual:
    spec = INSTRUMENT_SPECS[symbol]
    fee_rate = Decimal(str(all_in_cost_bps_each_side / 10_000.0))
    return CryptoPerpetual(
        instrument_id=instrument_id(symbol),
        raw_symbol=Symbol(symbol),
        base_currency=spec["base_currency"],
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=int(spec["price_precision"]),
        price_increment=Price.from_str(str(spec["price_increment"])),
        size_precision=int(spec["size_precision"]),
        size_increment=Quantity.from_str(str(spec["size_increment"])),
        max_quantity=Quantity.from_str("1000000000"),
        min_quantity=Quantity.from_str(str(spec["min_quantity"])),
        max_notional=None,
        min_notional=Money(10.00, USDT),
        max_price=Price.from_str("2000000"),
        min_price=Price.from_str(str(spec["price_increment"])),
        margin_init=Decimal("0.0500"),
        margin_maint=Decimal("0.0250"),
        maker_fee=fee_rate,
        taker_fee=fee_rate,
        ts_event=0,
        ts_init=0,
    )


def prepare_catalog(
    frames: dict[str, pd.DataFrame],
    raw_files: list[Path],
    catalog_path: Path,
    raw_cache: Path,
    output: Path,
    config: dict[str, Any],
) -> tuple[dict[str, CryptoPerpetual], Path]:
    if catalog_path.exists():
        shutil.rmtree(catalog_path)
    catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(catalog_path)
    instruments: dict[str, CryptoPerpetual] = {}
    bar_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        instrument = make_instrument(
            symbol,
            float(config["all_in_cost_bps_each_side"]),
        )
        bars = BarDataWrangler(bar_type(symbol), instrument).process(
            frames[symbol]
        )
        if not bars:
            raise MultiAssetRunnerError(
                f"BarDataWrangler produced no bars for {symbol}"
            )
        catalog.write_data([instrument])
        catalog.write_data(bars)
        instruments[symbol] = instrument
        bar_counts[symbol] = len(bars)

    manifest = build_data_manifest(
        raw_cache,
        dataset="binance-usdm-four-perpetuals-1m",
        include=raw_files,
        metadata_values={
            "symbols": list(SYMBOLS),
            "bar_types": {
                symbol: str(bar_type(symbol)) for symbol in SYMBOLS
            },
            "bars": bar_counts,
            "timestamp_semantics": "Binance close_time; completed bars only",
        },
    )
    manifest_path = output / "data_manifest.json"
    write_data_manifest(manifest_path, manifest)
    return instruments, manifest_path


def struct_fields(config_cls: type[Any]) -> set[str]:
    fields = getattr(config_cls, "__struct_fields__", None)
    if fields is not None:
        return set(fields)
    try:
        return set(inspect.signature(config_cls).parameters)
    except (TypeError, ValueError):
        return set()


def strategy_config(
    symbol: str,
    config: dict[str, Any],
    signals_root: Path,
    strategy_root: Path,
    coordinator_key: str,
) -> ImportableStrategyConfig:
    allowed = struct_fields(GlobalRichSignalConfig)
    values = {key: value for key, value in config.items() if key in allowed}
    required = {
        "instrument_id": str(instrument_id(symbol)),
        "bar_type": str(bar_type(symbol)),
        "signals_path": str(
            (signals_root / symbol / "signals.json").resolve()
        ),
        "global_instrument_ids": tuple(
            str(instrument_id(item)) for item in SYMBOLS
        ),
        "coordinator_key": coordinator_key,
    }
    for key, value in required.items():
        if not allowed or key in allowed:
            values[key] = value

    for key in ("output_dir", "output_path", "evidence_dir"):
        if key in allowed:
            values[key] = str((strategy_root / symbol).resolve())
    if "strategy_id" in allowed:
        values["strategy_id"] = f"C04-GLOBAL-{symbol}"

    missing = [
        key
        for key in ("instrument_id", "bar_type", "signals_path")
        if key in allowed and key not in values
    ]
    if missing:
        raise MultiAssetRunnerError(
            f"missing strategy config fields for {symbol}: {missing}"
        )
    return ImportableStrategyConfig(
        strategy_path=(
            "nt_global_rich_signal_strategy:GlobalRichSignalStrategy"
        ),
        config_path=(
            "nt_global_rich_signal_strategy:GlobalRichSignalConfig"
        ),
        config=values,
    )


def accepted_kwargs(cls: type[Any], values: dict[str, Any]) -> dict[str, Any]:
    try:
        parameters = inspect.signature(cls).parameters
    except (TypeError, ValueError):
        return values
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return values
    return {key: value for key, value in values.items() if key in parameters}


def build_run_config(
    catalog_path: Path,
    strategies: list[ImportableStrategyConfig],
    evaluation_start: date,
    evaluation_end: date,
    starting_nav: float,
) -> BacktestRunConfig:
    venue_values = {
        "name": "BINANCE",
        "oms_type": "NETTING",
        "account_type": "MARGIN",
        "base_currency": "USDT",
        "starting_balances": [f"{starting_nav:.8f} USDT"],
        "bar_execution": True,
        "trade_execution": False,
        "reject_stop_orders": False,
        "support_contingent_orders": True,
        "use_reduce_only": True,
    }
    venue = BacktestVenueConfig(
        **accepted_kwargs(BacktestVenueConfig, venue_values)
    )
    logging_values = {
        "log_level": "ERROR",
        "bypass_logging": False,
    }
    logging = LoggingConfig(
        **accepted_kwargs(LoggingConfig, logging_values)
    )
    engine = BacktestEngineConfig(
        **accepted_kwargs(
            BacktestEngineConfig,
            {"strategies": strategies, "logging": logging},
        )
    )
    data = []
    start_time = pd.Timestamp(evaluation_start, tz="UTC")
    end_time = pd.Timestamp(
        evaluation_end + timedelta(days=1),
        tz="UTC",
    )
    for symbol in SYMBOLS:
        values = {
            "catalog_path": str(catalog_path),
            "data_cls": "nautilus_trader.model.data:Bar",
            "instrument_id": str(instrument_id(symbol)),
            "start_time": start_time,
            "end_time": end_time,
        }
        data.append(
            BacktestDataConfig(
                **accepted_kwargs(BacktestDataConfig, values)
            )
        )
    return BacktestRunConfig(
        **accepted_kwargs(
            BacktestRunConfig,
            {"engine": engine, "venues": [venue], "data": data},
        )
    )


def get_engine(node: BacktestNode, result: Any) -> Any:
    candidates = [
        None,
        getattr(result, "run_config_id", None),
        getattr(result, "instance_id", None),
        getattr(result, "run_id", None),
    ]
    for candidate in candidates:
        try:
            engine = (
                node.get_engine()
                if candidate is None
                else node.get_engine(candidate)
            )
        except Exception:
            continue
        if engine is not None:
            return engine
    raise MultiAssetRunnerError("could not retrieve Nautilus backtest engine")


def report_frame(trader: Any, names: tuple[str, ...], *args: Any) -> pd.DataFrame:
    for name in names:
        method = getattr(trader, name, None)
        if not callable(method):
            continue
        try:
            value = method(*args)
        except TypeError:
            try:
                value = method()
            except Exception:
                continue
        except Exception:
            continue
        if isinstance(value, pd.DataFrame):
            return value
    return pd.DataFrame()


def event_timestamp(row: dict[str, Any]) -> int:
    candidates = [
        row.get("ts_event"),
        row.get("ts"),
        row.get("timestamp_ns"),
        (row.get("details") or {}).get("ts_event")
        if isinstance(row.get("details"), dict)
        else None,
        (row.get("details") or {}).get("ts")
        if isinstance(row.get("details"), dict)
        else None,
    ]
    for value in candidates:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def merge_json_rows(
    paths: list[Path],
    output: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    rows.sort(key=event_timestamp)
    output.write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return rows


def merge_equity(paths: list[Path], output: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for ordinal, path in enumerate(paths):
        if not path.exists() or path.stat().st_size == 0:
            continue
        frame = pd.read_csv(path)
        if not {"ts_event", "equity"}.issubset(frame.columns):
            continue
        frame["_source_order"] = ordinal
        frames.append(frame)
    if not frames:
        raise MultiAssetRunnerError("no strategy persisted portfolio equity")
    combined = pd.concat(frames, ignore_index=True)
    combined["ts_event"] = pd.to_numeric(
        combined["ts_event"], errors="raise"
    ).astype("int64")
    combined["equity"] = pd.to_numeric(
        combined["equity"], errors="raise"
    )
    combined = (
        combined.sort_values(["ts_event", "_source_order"])
        .drop_duplicates("ts_event", keep="last")
        .drop(columns=["_source_order"])
    )
    combined.to_csv(output, index=False)
    return single_base.read_equity(output)


def extract_positions(trader: Any) -> pd.DataFrame:
    return report_frame(
        trader,
        (
            "generate_positions_report",
            "generate_position_report",
        ),
    )


def realized_loss_fraction(
    positions: pd.DataFrame,
    events: list[dict[str, Any]],
) -> float:
    try:
        entries = [
            event
            for event in events
            if event.get("event_type") == "ENTRY_SUBMITTED"
        ]
        if len(entries) != len(positions.index):
            return float("nan")
        losses = []
        for position, entry in zip(
            positions.to_dict("records"),
            entries,
        ):
            pnl = float(
                str(position["realized_pnl"])
                .split()[0]
                .replace("_", "")
                .replace(",", "")
            )
            equity = float(entry["details"]["equity"])
            if pnl < 0.0:
                losses.append(abs(pnl) / equity)
        return max(losses, default=0.0)
    except Exception:
        return float("nan")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--signals-root", type=Path, required=True)
    parser.add_argument("--build-start", type=date.fromisoformat, required=True)
    parser.add_argument("--build-end", type=date.fromisoformat, required=True)
    parser.add_argument("--evaluation-start", type=date.fromisoformat, required=True)
    parser.add_argument("--evaluation-end", type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-trades", type=int, default=28)
    parser.add_argument("--min-active-days", type=int, default=14)
    parser.add_argument("--min-win-rate", type=float, default=0.55)
    parser.add_argument("--min-geometric-daily", type=float, default=0.01)
    args = parser.parse_args()

    if args.evaluation_start < args.build_start:
        raise MultiAssetRunnerError("evaluation starts before build range")
    if args.evaluation_end > args.build_end:
        raise MultiAssetRunnerError("evaluation ends after build range")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        path = args.signals_root / symbol / "signals.json"
        if not path.exists():
            raise MultiAssetRunnerError(f"missing signal file: {path}")

    frames: dict[str, pd.DataFrame] = {}
    raw_files: list[Path] = []
    for symbol in SYMBOLS:
        frame, evidence = load_symbol(
            symbol,
            args.build_start,
            args.build_end,
            args.cache / "raw",
        )
        frames[symbol] = frame
        raw_files.extend(evidence)

    catalog_path = args.cache / "catalog"
    _, manifest_path = prepare_catalog(
        frames,
        raw_files,
        catalog_path,
        args.cache / "raw",
        args.output,
        config,
    )
    coordinator_key = hashlib.sha256(
        (
            f"{args.output.resolve()}:{args.evaluation_start}:"
            f"{args.evaluation_end}"
        ).encode()
    ).hexdigest()
    reset_global_coordinator(coordinator_key)
    strategy_root = args.output / "strategies"
    strategies = [
        strategy_config(
            symbol,
            config,
            args.signals_root,
            strategy_root,
            coordinator_key,
        )
        for symbol in SYMBOLS
    ]
    run_config = build_run_config(
        catalog_path,
        strategies,
        args.evaluation_start,
        args.evaluation_end,
        float(config["starting_nav"]),
    )
    node = BacktestNode(configs=[run_config])
    results = node.run()
    if not results:
        raise MultiAssetRunnerError("NautilusTrader returned no run result")
    result = results[0]
    engine = get_engine(node, result)
    trader = engine.trader

    positions = extract_positions(trader)
    positions.to_csv(args.output / "positions.csv", index=False)
    orders = report_frame(
        trader,
        (
            "generate_order_fills_report",
            "generate_orders_report",
        ),
    )
    orders.to_csv(args.output / "orders.csv", index=False)
    account = report_frame(
        trader,
        (
            "generate_account_report",
            "generate_accounts_report",
        ),
        Venue("BINANCE"),
    )
    account.to_csv(args.output / "account.csv", index=False)

    strategy_dirs = [strategy_root / symbol for symbol in SYMBOLS]
    events = merge_json_rows(
        [path / "strategy_events.json" for path in strategy_dirs],
        args.output / "strategy_events.json",
    )
    merge_json_rows(
        [path / "closed_scenarios.json" for path in strategy_dirs],
        args.output / "closed_scenarios.json",
    )
    equity = merge_equity(
        [path / "equity.csv" for path in strategy_dirs],
        args.output / "equity.csv",
    )
    metrics = single_base.gate_metrics(
        equity,
        positions,
        args.output,
        args.evaluation_start,
        args.evaluation_end,
        config,
        result,
    )
    snapshot = coordinator_snapshot(coordinator_key)
    maximum_loss = realized_loss_fraction(positions, events)
    risk_pass = math.isfinite(maximum_loss) and maximum_loss <= 0.0301
    global_entry_pass = (
        int(snapshot["maximum_live_entries"]) <= 1
        and int(snapshot["invariant_violations"]) == 0
        and int(snapshot["live_count"]) == 0
    )
    alpha_checks = {
        "geometric_daily": float(metrics["geometric_daily_growth"])
        >= args.min_geometric_daily,
        "trades": int(metrics["trades"]) >= args.min_trades,
        "active_days": int(metrics["active_days"]) >= args.min_active_days,
        "win_rate": float(metrics["win_rate"]) >= args.min_win_rate,
        "positive_nav": float(metrics["ending_nav"]) > 0.0,
        "max_drawdown": float(metrics["max_drawdown"]) <= 0.20,
        "largest_winner_share": float(metrics["largest_winner_share"])
        <= 0.55,
    }
    metrics.update(
        {
            "candidate": "candidate-04-four-instrument-global-account",
            "symbols": list(SYMBOLS),
            "global_entry_coordinator": snapshot,
            "global_entry_pass": global_entry_pass,
            "maximum_realized_loss_fraction": maximum_loss,
            "risk_pass": risk_pass,
            "alpha_checks": alpha_checks,
            "candidate_pass": (
                global_entry_pass
                and risk_pass
                and all(alpha_checks.values())
            ),
            "data_manifest": str(manifest_path),
            "execution_contract": (
                "one NautilusTrader account; at most one pending new entry or "
                "open position across BTC ETH SOL XRP"
            ),
        }
    )
    write_json_atomic(args.output / "metrics.json", metrics)
    write_json_atomic(
        args.output / "run_manifest.json",
        {
            "engine": "NautilusTrader 1.230.0 BacktestNode",
            "symbols": list(SYMBOLS),
            "build_start": str(args.build_start),
            "build_end": str(args.build_end),
            "evaluation_start": str(args.evaluation_start),
            "evaluation_end": str(args.evaluation_end),
            "config": str(args.config),
            "signals_root": str(args.signals_root),
            "coordinator_key": coordinator_key,
            "data_manifest": str(manifest_path),
        },
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    node.dispose()


if __name__ == "__main__":
    main()
