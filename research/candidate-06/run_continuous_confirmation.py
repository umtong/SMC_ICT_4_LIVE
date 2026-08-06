"""Single-engine continuous 2025 BTC confirmation for a promoted candidate.

The script downloads official Binance USDT-M 1-minute archives, constructs the
same completed-bar side channel as weekly validation, and invokes the committed
``nautilus_runner.run_nautilus_backtest`` function.  It does not implement fills,
orders, portfolio accounting, or PnL outside NautilusTrader.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import inspect
import io
import json
import math
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_type_hints

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lrb_types import BarObservation
import market_data as market_data_module
import nautilus_runner as runner_module


BINANCE_ROOT = "https://data.binance.vision/data/futures/um/daily/klines"


def _download(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    temporary.replace(path)


def _verify_checksum(zip_path: Path, checksum_path: Path) -> str:
    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0].lower()
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
    if digest != expected:
        raise RuntimeError(f"checksum mismatch for {zip_path.name}: expected={expected} actual={digest}")
    return digest


def _load_day(day: date, cache: Path, symbol: str = "BTCUSDT") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stamp = day.isoformat()
    name = f"{symbol}-1m-{stamp}.zip"
    base = f"{BINANCE_ROOT}/{symbol}/1m/{name}"
    zip_path = cache / name
    checksum_path = cache / f"{name}.CHECKSUM"
    _download(base, zip_path)
    _download(base + ".CHECKSUM", checksum_path)
    digest = _verify_checksum(zip_path, checksum_path)
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_path) as archive:
        members = [member for member in archive.namelist() if member.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"expected one CSV in {name}, got {members}")
        text = io.TextIOWrapper(archive.open(members[0]), encoding="utf-8")
        reader = csv.reader(text)
        for raw in reader:
            if not raw:
                continue
            if not raw[0].isdigit():
                continue
            rows.append(
                {
                    "open_time_ms": int(raw[0]),
                    "open": float(raw[1]),
                    "high": float(raw[2]),
                    "low": float(raw[3]),
                    "close": float(raw[4]),
                    "volume": float(raw[5]),
                    "close_time_ms": int(raw[6]),
                    "quote_volume": float(raw[7]),
                    "trades": int(raw[8]),
                    "taker_buy_volume": float(raw[9]),
                    "taker_buy_quote_volume": float(raw[10]),
                }
            )
    if len(rows) != 1440:
        raise RuntimeError(f"{name}: expected 1440 rows, got {len(rows)}")
    return rows, {"date": stamp, "archive": name, "sha256": digest, "rows": len(rows)}


def _load_period(start: date, end_exclusive: date, cache: Path) -> tuple[pd.DataFrame, dict[int, BarObservation], list[dict[str, Any]]]:
    all_rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    current = start
    while current < end_exclusive:
        rows, record = _load_day(current, cache)
        all_rows.extend(rows)
        manifest.append(record)
        current += timedelta(days=1)
    expected = (end_exclusive - start).days * 1440
    if len(all_rows) != expected:
        raise RuntimeError(f"period expected {expected} bars, got {len(all_rows)}")
    frame = pd.DataFrame(all_rows)
    completed_ms = frame["open_time_ms"].astype("int64") + 60_000
    frame.index = pd.to_datetime(completed_ms, unit="ms", utc=True)
    ohlcv = frame[["open", "high", "low", "close", "volume"]].copy()
    ohlcv.index.name = "timestamp"
    observations: dict[int, BarObservation] = {}
    for row in frame.itertuples(index=False):
        ts_ns = (int(row.open_time_ms) + 60_000) * 1_000_000
        observations[ts_ns] = BarObservation(
            ts_ns=ts_ns,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            taker_buy_volume=float(row.taker_buy_volume),
            trades=int(row.trades),
        )
    return ohlcv, observations, manifest


def _value_for_name(name: str, context: dict[str, Any]) -> Any:
    lowered = name.lower()
    if name in context:
        return context[name]
    if "observation" in lowered:
        return context["observations"]
    if lowered in {"frame", "df", "dataframe", "ohlcv", "bars_frame"}:
        return context["frame"]
    if lowered in {"config", "candidate_config", "settings"}:
        return context["config"]
    if "logic" in lowered and "param" in lowered:
        return context["logic_params"]
    if "final" in lowered and ("ts" in lowered or "time" in lowered):
        return context["final_ts_ns"]
    if "output" in lowered or lowered in {"out", "artifact_dir"}:
        return context["output"]
    if "manifest" in lowered:
        return context["manifest"]
    if lowered in {"start", "start_date", "start_utc"}:
        return context["start"]
    if lowered in {"end", "end_date", "end_utc", "end_exclusive"}:
        return context["end"]
    raise KeyError(name)


def _build_dataclass(cls: type[Any], context: dict[str, Any]) -> Any:
    values: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        try:
            values[field.name] = _value_for_name(field.name, context)
        except KeyError:
            if field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING:  # type: ignore[attr-defined]
                continue
            lowered = field.name.lower()
            if "symbol" in lowered:
                values[field.name] = "BTCUSDT"
            elif "interval" in lowered:
                values[field.name] = "1m"
            elif "row" in lowered or "bar" in lowered:
                values[field.name] = len(context["frame"])
            else:
                raise RuntimeError(f"cannot construct {cls.__name__}: unknown required field {field.name}")
    return cls(**values)


def _find_bundle(context: dict[str, Any]) -> Any | None:
    for value in vars(market_data_module).values():
        if inspect.isclass(value) and dataclasses.is_dataclass(value):
            names = {field.name.lower() for field in dataclasses.fields(value)}
            if any("observation" in name for name in names) and any(name in {"frame", "dataframe", "ohlcv", "bars"} for name in names):
                try:
                    return _build_dataclass(value, context)
                except Exception:
                    continue
    return None


def _invoke_runner(context: dict[str, Any]) -> Any:
    function = getattr(runner_module, "run_nautilus_backtest")
    signature = inspect.signature(function)
    try:
        hints = get_type_hints(function)
    except Exception:
        hints = {}
    bundle = _find_bundle(context)
    kwargs: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        try:
            kwargs[name] = _value_for_name(name, context)
            continue
        except KeyError:
            pass
        annotation = hints.get(name, parameter.annotation)
        if inspect.isclass(annotation) and dataclasses.is_dataclass(annotation):
            kwargs[name] = _build_dataclass(annotation, context)
            continue
        if bundle is not None and any(token in name.lower() for token in ("data", "week", "market", "bundle")):
            kwargs[name] = bundle
            continue
        if parameter.default is not inspect.Parameter.empty:
            continue
        raise RuntimeError(f"unsupported run_nautilus_backtest parameter: {name} annotation={annotation!r}")
    return function(**kwargs)


def _find_strategy(result: Any) -> Any:
    if result is None:
        raise RuntimeError("run_nautilus_backtest returned None")
    for name in ("strategy", "candidate_strategy", "trading_strategy"):
        if hasattr(result, name):
            return getattr(result, name)
        if isinstance(result, dict) and name in result:
            return result[name]
    if hasattr(result, "closed_trades") and hasattr(result, "equity_samples"):
        return result
    if isinstance(result, (tuple, list)):
        for value in result:
            try:
                return _find_strategy(value)
            except RuntimeError:
                pass
    raise RuntimeError(f"cannot locate strategy in runner result type={type(result).__name__}")


def _pnl(trade: dict[str, Any]) -> float:
    for key in ("realized_pnl_after_cost", "realized_pnl", "net_pnl", "pnl"):
        if key in trade and trade[key] is not None:
            return float(trade[key])
    return 0.0


def _nav(sample: dict[str, Any]) -> float:
    for key in ("nav", "equity", "total_equity", "value"):
        if key in sample and sample[key] is not None:
            return float(sample[key])
    raise KeyError("no NAV field")


def _ts(sample: dict[str, Any]) -> int:
    for key in ("ts_ns", "timestamp_ns", "event_time_ns"):
        if key in sample and sample[key] is not None:
            return int(sample[key])
    return 0


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0.0:
            maximum = max(maximum, (peak - value) / peak)
    return maximum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end-exclusive", default="2026-01-01")
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end_exclusive)
    frame, observations, manifest = _load_period(start, end, output / "data-cache")
    final_ts_ns = int(frame.index[-1].value)
    context = {
        "frame": frame,
        "observations": observations,
        "config": config,
        "logic_params": config["logic"],
        "final_ts_ns": final_ts_ns,
        "output": output,
        "manifest": manifest,
        "start": start,
        "end": end,
    }
    (output / "data_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        result = _invoke_runner(context)
        strategy = _find_strategy(result)
        trades = [dict(value) for value in getattr(strategy, "closed_trades", [])]
        samples = [dict(value) for value in getattr(strategy, "equity_samples", [])]
        errors = [str(value) for value in getattr(strategy, "errors", [])]
        diagnostics = dict(getattr(strategy, "diagnostics", {}))
        nav_values = [_nav(sample) for sample in samples]
        configured_start = float(config.get("execution", {}).get("starting_balance", 100000.0))
        starting_nav = nav_values[0] if nav_values else configured_start
        ending_nav = nav_values[-1] if nav_values else starting_nav
        pnls = [_pnl(trade) for trade in trades]
        wins = sum(value > 0.0 for value in pnls)
        losses = sum(value < 0.0 for value in pnls)
        gross_profit = sum(value for value in pnls if value > 0.0)
        gross_loss = -sum(value for value in pnls if value < 0.0)
        profit_factor: float | str = gross_profit / gross_loss if gross_loss > 0.0 else ("Infinity" if gross_profit > 0.0 else 0.0)
        days = (end - start).days
        growth = (ending_nav / starting_nav) ** (1.0 / days) - 1.0 if ending_nav > 0.0 and starting_nav > 0.0 else -1.0
        positive = sorted((value for value in pnls if value > 0.0), reverse=True)
        positive_sum = sum(positive)
        concentration = positive[0] / positive_sum if positive_sum > 0.0 else 1.0
        failures: list[str] = []
        if growth < 0.01:
            failures.append("GEOMETRIC_DAILY_NAV_GROWTH_BELOW_1_PERCENT")
        if len(trades) / days < 1.0:
            failures.append("TRADES_PER_DAY_BELOW_1")
        if (wins / len(trades) if trades else 0.0) < 0.45:
            failures.append("WIN_RATE_BELOW_45_PERCENT")
        if _max_drawdown(nav_values) > 0.25:
            failures.append("MAX_DRAWDOWN_ABOVE_25_PERCENT")
        if concentration > 0.10:
            failures.append("PROFIT_CONCENTRATION_ABOVE_10_PERCENT")
        if errors:
            failures.append("STRATEGY_ERRORS_PRESENT")
        summary = {
            "method": "single continuous NautilusTrader engine and portfolio NAV",
            "period_start": args.start,
            "period_end_exclusive": args.end_exclusive,
            "bars": len(frame),
            "starting_nav": starting_nav,
            "ending_nav": ending_nav,
            "total_nav_return": ending_nav / starting_nav - 1.0,
            "geometric_daily_nav_growth": growth,
            "trades": len(trades),
            "trades_per_day": len(trades) / days,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(trades) if trades else 0.0,
            "profit_factor": profit_factor,
            "max_drawdown_nav": _max_drawdown(nav_values),
            "largest_positive_trade_share": concentration,
            "gate_passed": not failures,
            "gate_failures": failures,
            "errors": errors,
            "diagnostics": diagnostics,
        }
        (output / "metrics.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with (output / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
            fields = sorted({key for trade in trades for key in trade})
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(trades)
        with (output / "equity.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["ts_ns", "nav"])
            for sample in samples:
                writer.writerow([_ts(sample), _nav(sample)])
        lines = ["# Candidate 06 continuous 2025 confirmation", "", "| metric | value |", "|---|---:|"]
        for key in (
            "bars", "starting_nav", "ending_nav", "total_nav_return", "geometric_daily_nav_growth",
            "trades", "trades_per_day", "win_rate", "profit_factor", "max_drawdown_nav",
            "largest_positive_trade_share", "gate_passed",
        ):
            lines.append(f"| `{key}` | `{summary[key]}` |")
        lines.extend(["", "## Gate failures", ""])
        lines.extend([f"- `{value}`" for value in failures] or ["- none"])
        (output / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0 if not failures else 2
    except Exception as exc:
        failure = {
            "method": "single continuous NautilusTrader engine and portfolio NAV",
            "gate_passed": False,
            "gate_failures": ["CONTINUOUS_RUN_IMPLEMENTATION_OR_RUNTIME_FAILURE"],
            "error": f"{type(exc).__name__}: {exc}",
            "runner_signature": str(inspect.signature(getattr(runner_module, "run_nautilus_backtest"))),
        }
        (output / "metrics.json").write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output / "SUMMARY.md").write_text(
            "# Candidate 06 continuous confirmation failure\n\n"
            f"- error: `{failure['error']}`\n"
            f"- runner signature: `{failure['runner_signature']}`\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
