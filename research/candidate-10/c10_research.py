"""Reproducible data acquisition, Nautilus backtest, and metrics for candidate 10."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import csv
import io
from pathlib import Path
import random
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import zipfile

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
try:
    from nautilus_trader.model import CryptoPerpetual
except ImportError:
    from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import build_data_manifest
from smc_ict_4.manifest import create_run_manifest
from smc_ict_4.manifest import write_data_manifest
from smc_ict_4.manifest import write_json_atomic

from c10_model import MachineParams
from c10_model import MS_PER_MINUTE
from c10_model import NS_PER_MINUTE
from c10_strategy import Candidate10Config
from c10_strategy import Candidate10Strategy
from c10_strategy import make_cost_loaded_btc_perpetual

BINANCE_DATA_ROOT = "https://data.binance.vision/data/futures/um/daily/klines"
KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)


def reproducible_weeks(seed: int = 20260806) -> list[date]:
    start = date(2022, 1, 3)
    end = date(2024, 12, 23)
    mondays: list[date] = []
    cursor = start
    while cursor <= end:
        mondays.append(cursor)
        cursor += timedelta(days=7)
    return random.Random(seed).sample(mondays, 3)


def _download_bytes(url: str, attempts: int = 4) -> bytes:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(url, timeout=45) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {error}")


def download_binance_week(
    week_start: date,
    destination: str | Path,
    *,
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    warmup_days: int = 1,
) -> tuple[list[Path], dict[str, Any]]:
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    first = week_start - timedelta(days=warmup_days)
    last_exclusive = week_start + timedelta(days=7)
    paths: list[Path] = []
    records: list[dict[str, Any]] = []
    cursor = first
    while cursor < last_exclusive:
        stem = f"{symbol}-{interval}-{cursor.isoformat()}"
        base = f"{BINANCE_DATA_ROOT}/{symbol}/{interval}/{stem}.zip"
        zip_path = root / f"{stem}.zip"
        checksum_path = root / f"{stem}.zip.CHECKSUM"
        if not zip_path.exists():
            zip_path.write_bytes(_download_bytes(base))
        if not checksum_path.exists():
            checksum_path.write_bytes(_download_bytes(base + ".CHECKSUM"))
        expected = checksum_path.read_text(encoding="utf-8").strip().split()[0]
        actual = sha256(zip_path.read_bytes()).hexdigest()
        if actual.lower() != expected.lower():
            raise RuntimeError(
                f"checksum mismatch for {zip_path.name}: {actual} != {expected}",
            )
        paths.extend([zip_path, checksum_path])
        records.append(
            {"date": cursor.isoformat(), "zip": zip_path.name, "sha256": actual},
        )
        cursor += timedelta(days=1)
    return paths, {
        "provider": "Binance public data",
        "market": "USD-M futures",
        "symbol": symbol,
        "interval": interval,
        "week_start": week_start.isoformat(),
        "warmup_days": warmup_days,
        "files": records,
    }


def load_bars(
    paths: Iterable[Path],
    instrument: CryptoPerpetual,
) -> tuple[list[Bar], dict[str, Any]]:
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    rows: dict[int, tuple[str, ...]] = {}
    rows_read = 0
    duplicate_open_times = 0
    for path in sorted((item for item in paths if item.suffix == ".zip"), key=str):
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(members) != 1:
                raise RuntimeError(f"expected one CSV in {path}, found {members}")
            text = io.TextIOWrapper(archive.open(members[0]), encoding="utf-8")
            reader = csv.reader(text)
            for row in reader:
                if not row or not row[0].lstrip("-").isdigit():
                    continue
                if len(row) < len(KLINE_COLUMNS):
                    raise RuntimeError(f"short kline row in {path}: {row}")
                rows_read += 1
                open_time = int(row[0])
                if open_time in rows:
                    duplicate_open_times += 1
                rows[open_time] = tuple(row[: len(KLINE_COLUMNS)])

    ordered = sorted(rows.items())
    if not ordered:
        raise RuntimeError("no kline rows loaded")
    gaps: list[dict[str, int]] = []
    bars: list[Bar] = []
    previous_open: int | None = None
    for open_ms, row in ordered:
        if previous_open is not None and open_ms - previous_open != MS_PER_MINUTE:
            gaps.append({"previous_open_ms": previous_open, "open_ms": open_ms})
        previous_open = open_ms
        ts_ns = (open_ms + MS_PER_MINUTE) * 1_000_000
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(float(row[1]), precision=instrument.price_precision),
                high=Price(float(row[2]), precision=instrument.price_precision),
                low=Price(float(row[3]), precision=instrument.price_precision),
                close=Price(float(row[4]), precision=instrument.price_precision),
                volume=Quantity(float(row[5]), precision=instrument.size_precision),
                ts_event=ts_ns,
                ts_init=ts_ns,
            ),
        )
    return bars, {
        "bar_count": len(bars),
        "first_open_ms": ordered[0][0],
        "last_open_ms": ordered[-1][0],
        "rows_read": rows_read,
        "duplicate_open_times_removed": duplicate_open_times,
        "gap_count": len(gaps),
        "gaps": gaps[:100],
        "timestamp_semantics": (
            "ts_event is open_time + 60 seconds; bar known only at close"
        ),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _daily_metrics(
    starting_balance: float,
    daily_nav: dict[str, float],
) -> dict[str, Any]:
    ordered = sorted(daily_nav.items())
    values = [starting_balance] + [value for _, value in ordered]
    returns = [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
    ]
    days = len(returns)
    geometric = (
        (values[-1] / values[0]) ** (1.0 / days) - 1.0
        if days and values[-1] > 0
        else -1.0
    )
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = max(max_dd, 1.0 - value / peak)
    return {
        "daily_nav": [{"date": key, "nav": value} for key, value in ordered],
        "daily_returns": returns,
        "evaluation_days": days,
        "geometric_daily_growth": geometric,
        "daily_max_drawdown": max_dd,
        "positive_days": sum(value > 0 for value in returns),
        "negative_days": sum(value < 0 for value in returns),
        "flat_days": sum(value == 0 for value in returns),
    }


def run_backtest(
    *,
    week_start: date,
    variant: str,
    params: MachineParams,
    output_dir: str | Path,
    data_root: str | Path,
    starting_balance: Decimal = Decimal("100000"),
    risk_fraction: Decimal = Decimal("0.03"),
    seed: int = 20260806,
) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    instrument = make_cost_loaded_btc_perpetual()
    downloaded, source_metadata = download_binance_week(week_start, data_root)
    data_manifest = build_data_manifest(
        data_root,
        dataset=f"binance-um-BTCUSDT-1m-{week_start.isoformat()}",
        include=downloaded,
        metadata_values=source_metadata,
    )
    data_manifest_path = destination / "data_manifest.json"
    write_data_manifest(data_manifest_path, data_manifest)

    bars, quality = load_bars(downloaded, instrument)
    if quality["gap_count"]:
        raise RuntimeError(
            f"market data has {quality['gap_count']} gaps; refusing to backtest",
        )

    eval_start_ns = int(
        datetime.combine(
            week_start,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9,
    )
    eval_end_ns = eval_start_ns + 7 * 24 * 60 * NS_PER_MINUTE
    bar_type = BarType.from_str(
        "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
    )
    strategy = Candidate10Strategy(
        Candidate10Config(
            instrument_id=instrument.id,
            bar_type=bar_type,
            eval_start_ns=eval_start_ns,
            eval_end_ns=eval_end_ns,
            risk_fraction=risk_fraction,
            params={
                name: getattr(params, name)
                for name in params.__dataclass_fields__
            },
            starting_balance=starting_balance,
        ),
    )
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    run_id = f"candidate-10-{variant}-{week_start.isoformat()}"
    try:
        usdt = Currency.from_str("USDT")
        engine.add_venue(
            venue=Venue("BINANCE"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(starting_balance, usdt)],
            base_currency=usdt,
            default_leverage=Decimal("20"),
            fill_model=FillModel(
                prob_fill_on_limit=1.0,
                prob_slippage=1.0,
                random_seed=seed,
            ),
            support_contingent_orders=True,
            bar_execution=True,
            bar_adaptive_high_low_ordering=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(Venue("BINANCE"))
        fills.to_csv(destination / "orders.csv", index=False)
        positions.to_csv(destination / "positions.csv", index=False)
        account.to_csv(destination / "account.csv", index=False)

        write_events(destination / "scenario_events.jsonl", strategy.events)
        _write_csv(destination / "trades.csv", strategy.trade_records)
        _write_csv(destination / "equity_curve.csv", strategy.equity_curve)
        _write_csv(destination / "order_errors.csv", strategy.order_errors)

        end_equity = strategy._equity()
        daily = _daily_metrics(float(starting_balance), strategy.daily_nav)
        wins = [row for row in strategy.trade_records if row["net_pnl"] > 0]
        losses = [row for row in strategy.trade_records if row["net_pnl"] < 0]
        scenario_pnl: dict[str, float] = defaultdict(float)
        for row in strategy.trade_records:
            scenario_pnl[str(row["scenario"])] += float(row["net_pnl"])
        positive_pnls = sorted(
            (float(row["net_pnl"]) for row in wins),
            reverse=True,
        )
        positive_total = sum(positive_pnls)
        concentration = (
            positive_pnls[0] / positive_total
            if positive_pnls and positive_total
            else 0.0
        )
        metrics = {
            "run_id": run_id,
            "candidate": "candidate-10",
            "variant": variant,
            "week_start": week_start.isoformat(),
            "starting_nav": float(starting_balance),
            "ending_nav": end_equity,
            "net_return": end_equity / float(starting_balance) - 1.0,
            "intraday_max_drawdown": strategy.max_drawdown,
            "signals_seen": strategy.signals_seen,
            "signals_outside_evaluation": strategy.signals_outside_evaluation,
            "orders_submitted": strategy.orders_submitted,
            "closed_trades": len(strategy.trade_records),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (
                len(wins) / len(strategy.trade_records)
                if strategy.trade_records
                else 0.0
            ),
            "profit_concentration_largest_win": concentration,
            "scenario_net_pnl": dict(scenario_pnl),
            "forced_exits": strategy.forced_exits,
            "order_error_count": len(strategy.order_errors),
            "data_quality": quality,
            "cost_model": {
                "maker_fee_plus_execution_reserve": str(instrument.maker_fee),
                "taker_fee_plus_execution_reserve": str(instrument.taker_fee),
                "fill_model_prob_slippage": 1.0,
                "funding": (
                    "positions forced flat before 00:00, 08:00, 16:00 UTC windows"
                ),
            },
            "risk": {
                "risk_fraction": str(risk_fraction),
                "sizing_basis": "current Nautilus portfolio equity",
                "arbitrary_notional_cap": False,
                "default_leverage": "20 derived from 5% instrument initial margin",
            },
            "params": {
                name: getattr(params, name)
                for name in params.__dataclass_fields__
            },
            **daily,
        }
        metrics["target_pass"] = bool(
            metrics["geometric_daily_growth"] >= 0.01
            and metrics["closed_trades"] >= 7
            and metrics["wins"] >= 4
            and metrics["profit_concentration_largest_win"] <= 0.50
            and metrics["order_error_count"] == 0
            and metrics["intraday_max_drawdown"] < 0.30
        )
        write_json_atomic(destination / "metrics.json", metrics)
        write_json_atomic(
            destination / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate="candidate-10",
                data_manifest_path=data_manifest_path,
                extra={
                    "variant": variant,
                    "week_start": week_start.isoformat(),
                    "engine": "NautilusTrader BacktestEngine",
                    "data_quality": quality,
                },
            ),
        )
        return metrics
    finally:
        engine.dispose()
