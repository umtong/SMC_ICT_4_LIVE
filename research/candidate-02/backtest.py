"""Reproducible Binance Vision -> NautilusTrader validation pipeline."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import random
import re
import urllib.error
import urllib.request
import zipfile
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler

from smc_ict_4.contracts import ResearchEvent
from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, sha256_file, write_json_atomic

from core import CandidateConfig, geometric_daily_growth, max_drawdown
from strategy import Candidate02Strategy, Candidate02StrategyConfig


BINANCE_VISION = "https://data.binance.vision/data/futures/um"
KLINE_COLUMNS = [
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
]
ALLOWED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class _WritableValuesFrame(pd.DataFrame):
    """Pandas 3 CoW compatibility for NautilusTrader 1.230.0 wranglers.

    NautilusTrader's Cython memoryview requires a writable ndarray, while
    pandas 3 deliberately exposes ``DataFrame.values`` as read-only. Return a
    fresh writable float64 array without changing source market data.
    """

    @property
    def _constructor(self):
        return _WritableValuesFrame

    @property
    def values(self):
        array = self.to_numpy(dtype="float64", copy=True)
        array.setflags(write=True)
        return array
NS_MINUTE = 60_000_000_000
UTC = timezone.utc


INSTRUMENT_SPECS: dict[str, dict[str, str | int]] = {
    "BTCUSDT": {
        "price_precision": 1,
        "size_precision": 3,
        "price_increment": "0.1",
        "size_increment": "0.001",
        "min_quantity": "0.001",
        "max_quantity": "1000.000",
        "min_price": "100.0",
        "max_price": "1000000.0",
    },
    "ETHUSDT": {
        "price_precision": 2,
        "size_precision": 3,
        "price_increment": "0.01",
        "size_increment": "0.001",
        "min_quantity": "0.001",
        "max_quantity": "10000.000",
        "min_price": "1.00",
        "max_price": "100000.00",
    },
    "SOLUSDT": {
        "price_precision": 3,
        "size_precision": 0,
        "price_increment": "0.001",
        "size_increment": "1",
        "min_quantity": "1",
        "max_quantity": "1000000",
        "min_price": "0.001",
        "max_price": "100000.000",
    },
    "XRPUSDT": {
        "price_precision": 4,
        "size_precision": 1,
        "price_increment": "0.0001",
        "size_increment": "0.1",
        "min_quantity": "0.1",
        "max_quantity": "100000000.0",
        "min_price": "0.0001",
        "max_price": "10000.0000",
    },
}


def _iso_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _utc_midnight(value: str | date | datetime) -> datetime:
    parsed = _iso_date(value)
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict"):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    return str(value)


def deterministic_week_selection(
    *,
    seed: int,
    population_start: str,
    population_end: str,
    count: int = 3,
) -> list[str]:
    start = _iso_date(population_start)
    end = _iso_date(population_end)
    if start.weekday() != 0 or end.weekday() != 0:
        raise ValueError("week population endpoints must be Mondays")
    weeks: list[str] = []
    current = start
    while current <= end:
        weeks.append(current.isoformat())
        current += timedelta(days=7)
    if count > len(weeks):
        raise ValueError("sample count exceeds week population")
    return random.Random(seed).sample(weeks, count)


def make_instrument(
    symbol: str,
    *,
    maker_fee: Decimal,
    taker_fee: Decimal,
) -> CryptoPerpetual:
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError(f"unsupported instrument: {symbol}")
    spec = INSTRUMENT_SPECS[symbol]
    usdt = Currency.from_str("USDT")
    base = Currency.from_str(symbol.removesuffix("USDT"))
    instrument_id = InstrumentId(Symbol(f"{symbol}-PERP"), Venue("BINANCE"))
    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=int(spec["price_precision"]),
        size_precision=int(spec["size_precision"]),
        price_increment=Price.from_str(str(spec["price_increment"])),
        size_increment=Quantity.from_str(str(spec["size_increment"])),
        ts_event=0,
        ts_init=0,
        max_quantity=Quantity.from_str(str(spec["max_quantity"])),
        min_quantity=Quantity.from_str(str(spec["min_quantity"])),
        min_notional=Money(5.0, usdt),
        max_price=Price.from_str(str(spec["max_price"])),
        min_price=Price.from_str(str(spec["min_price"])),
        margin_init=Decimal("0.02"),
        margin_maint=Decimal("0.01"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )


def bar_type_for(instrument: CryptoPerpetual) -> BarType:
    return BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")


def _download(url: str, destination: Path, retries: int = 3) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SMC-ICT-4-candidate-02/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                if getattr(response, "status", 200) != 200:
                    raise RuntimeError(f"HTTP {response.status} for {url}")
                temporary.write_bytes(response.read())
            if temporary.stat().st_size == 0:
                raise RuntimeError(f"empty download: {url}")
            temporary.replace(destination)
            return destination
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt + 1 == retries:
                break
    raise RuntimeError(f"failed to download {url}: {last_error}")


def _daily_url(symbol: str, day: date) -> str:
    stamp = day.isoformat()
    return f"{BINANCE_VISION}/daily/klines/{symbol}/1m/{symbol}-1m-{stamp}.zip"


def _monthly_url(symbol: str, year: int, month: int) -> str:
    stamp = f"{year:04d}-{month:02d}"
    return f"{BINANCE_VISION}/monthly/klines/{symbol}/1m/{symbol}-1m-{stamp}.zip"


def _last_day_of_month(day: date) -> date:
    next_month = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


def _archive_plan(symbol: str, start_day: date, end_day: date) -> list[tuple[str, Path, str]]:
    """Prefer monthly archives for whole months, daily archives at boundaries."""
    if end_day < start_day:
        raise ValueError("end_day precedes start_day")
    plan: list[tuple[str, Path, str]] = []
    current = start_day
    while current <= end_day:
        month_end = _last_day_of_month(current)
        whole_month = current.day == 1 and month_end <= end_day
        if whole_month:
            url = _monthly_url(symbol, current.year, current.month)
            filename = Path(symbol) / "monthly" / Path(url).name
            plan.append((url, filename, "monthly"))
            current = month_end + timedelta(days=1)
        else:
            url = _daily_url(symbol, current)
            filename = Path(symbol) / "daily" / Path(url).name
            plan.append((url, filename, "daily"))
            current += timedelta(days=1)
    return plan


def _read_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {members}")
        with archive.open(members[0]) as stream:
            raw = stream.read()
    frame = pd.read_csv(io.BytesIO(raw), header=None)
    if frame.empty:
        raise ValueError(f"empty kline archive: {path}")
    if str(frame.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape[1] < len(KLINE_COLUMNS):
        raise ValueError(f"unexpected kline column count in {path}: {frame.shape[1]}")
    frame = frame.iloc[:, : len(KLINE_COLUMNS)]
    frame.columns = KLINE_COLUMNS
    return frame


def _timestamps_to_ns(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    maximum = int(numeric.max())
    # Binance Vision transitioned some archives from milliseconds to
    # microseconds.  Normalize either representation without consulting future
    # rows for any trading decision; this is data ingestion only.
    if maximum >= 100_000_000_000_000:
        return numeric * 1_000
    return numeric * 1_000_000


def load_binance_1m(
    symbol: str,
    *,
    start: datetime,
    end: datetime,
    cache_root: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start/end must be timezone-aware")
    start_day = start.astimezone(UTC).date()
    end_day = end.astimezone(UTC).date()
    frames: list[pd.DataFrame] = []
    files: list[dict[str, Any]] = []
    for url, relative, archive_kind in _archive_plan(symbol, start_day, end_day):
        path = _download(url, cache_root / relative)
        frame = _read_archive(path)
        frames.append(frame)
        files.append(
            {
                "symbol": symbol,
                "archive_kind": archive_kind,
                "path": str(path),
                "source_url": url,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            },
        )
    combined = pd.concat(frames, ignore_index=True)
    open_ns = _timestamps_to_ns(combined["open_time"])
    combined["close_ns"] = open_ns + NS_MINUTE
    start_ns, end_ns = _ns(start), _ns(end)
    combined = combined[(combined["close_ns"] >= start_ns) & (combined["close_ns"] <= end_ns)].copy()
    if combined.empty:
        raise ValueError(f"no bars for {symbol} in {start.isoformat()}..{end.isoformat()}")
    if combined["close_ns"].duplicated().any():
        duplicates = int(combined["close_ns"].duplicated().sum())
        raise ValueError(f"duplicate bar closes for {symbol}: {duplicates}")
    combined.sort_values("close_ns", inplace=True)
    gaps = combined["close_ns"].diff().dropna()
    missing_minutes = int(((gaps // NS_MINUTE) - 1).clip(lower=0).sum())
    expected = int((end_ns - start_ns) // NS_MINUTE) + 1
    coverage = len(combined) / expected if expected else 1.0
    if coverage < 0.995:
        raise ValueError(
            f"insufficient {symbol} coverage: {len(combined)}/{expected} ({coverage:.4%}), "
            f"missing_minutes={missing_minutes}",
        )
    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        combined[column] = pd.to_numeric(combined[column], errors="raise").astype("float64")
    if not (
        (combined["low"] <= combined[["open", "close"]].min(axis=1)).all()
        and (combined["high"] >= combined[["open", "close"]].max(axis=1)).all()
        and (combined["high"] >= combined["low"]).all()
        and (combined["volume"] >= 0).all()
    ):
        raise ValueError(f"OHLCV integrity failure for {symbol}")
    index = pd.to_datetime(combined["close_ns"], unit="ns", utc=True)
    result = combined[numeric_columns].copy()
    result.index = index
    result.index.name = "close_time_utc"
    for item in files:
        item.update(
            {
                "requested_start_utc": start.isoformat(),
                "requested_end_utc": end.isoformat(),
                "loaded_rows_total": len(result),
                "coverage": coverage,
                "missing_minutes": missing_minutes,
            },
        )
    return result, files


def _parse_money(value: Any) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else 0.0


def _write_records(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(_json_safe(record), sort_keys=True, ensure_ascii=False))
            stream.write("\n")


def _transitions_to_events(
    transitions: Sequence[Any],
    *,
    scenario_prefix: str,
) -> list[ResearchEvent]:
    events: list[ResearchEvent] = []
    for item in transitions:
        events.append(
            ResearchEvent(
                scenario_id=f"{scenario_prefix}:{item.scenario_id}",
                instrument_id=item.instrument_id,
                event_type=item.event_type,
                event_time_ns=int(item.event_time_ns),
                observed_time_ns=int(item.observed_time_ns),
                previous_state=item.previous_state,
                next_state=item.next_state,
                reason_code=item.reason_code,
                reference_price=(str(item.reference_price) if item.reference_price is not None else None),
                details=dict(item.details),
            ),
        )
    return events


def _effective_commission_rates(execution: Mapping[str, str]) -> tuple[Decimal, Decimal]:
    actual_maker = Decimal(execution["maker_fee_rate"])
    actual_taker = Decimal(execution["taker_fee_rate"])
    entry_slippage = Decimal(execution["entry_slippage_rate"])
    stop_slippage = Decimal(execution["stop_slippage_rate"])
    impact = Decimal(execution["market_impact_rate"])
    funding = Decimal(execution["funding_rate_allowance"])
    # Bar data has no spread or queue depth.  Charge those costs through the
    # engine's commission path so reported NAV is cost-after, rather than merely
    # recording them in a side calculation.  The stop-side estimate is used for
    # all taker fills, which is conservative for market entries.
    effective_taker = actual_taker + max(entry_slippage, stop_slippage) + impact + funding / 2
    effective_maker = actual_maker + impact + funding / 2
    return effective_maker, effective_taker


def run_window(
    *,
    label: str,
    symbols: Sequence[str],
    evaluation_start: datetime,
    evaluation_end: datetime,
    config: Mapping[str, Any],
    output: Path,
    cache_root: Path,
) -> dict[str, Any]:
    if any(symbol not in ALLOWED_SYMBOLS for symbol in symbols):
        raise ValueError(f"symbols must be drawn from {ALLOWED_SYMBOLS}")
    if len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be unique")
    output.mkdir(parents=True, exist_ok=True)
    warmup_days = int(config["validation"]["warmup_days"])
    buffer_minutes = int(config["validation"]["exit_buffer_minutes"])
    data_start = evaluation_start - timedelta(days=warmup_days)
    data_end = evaluation_end + timedelta(minutes=buffer_minutes)

    execution = config["execution"]
    effective_maker, effective_taker = _effective_commission_rates(execution)
    instruments = [
        make_instrument(symbol, maker_fee=effective_maker, taker_fee=effective_taker)
        for symbol in symbols
    ]
    bar_types = [bar_type_for(instrument) for instrument in instruments]
    all_bars: list[Any] = []
    data_files: list[dict[str, Any]] = []
    integrity: dict[str, Any] = {}
    for symbol, instrument, bar_type in zip(symbols, instruments, bar_types, strict=True):
        frame, files = load_binance_1m(
            symbol,
            start=data_start,
            end=data_end,
            cache_root=cache_root,
        )
        bars = BarDataWrangler(bar_type, instrument).process(_WritableValuesFrame(frame))
        if len(bars) != len(frame):
            raise RuntimeError(f"wrangler row mismatch for {symbol}: {len(frame)} -> {len(bars)}")
        all_bars.extend(bars)
        data_files.extend(files)
        integrity[symbol] = {
            "rows": len(frame),
            "first_close_utc": frame.index[0].isoformat(),
            "last_close_utc": frame.index[-1].isoformat(),
            "bar_type": str(bar_type),
            "instrument_id": str(instrument.id),
        }

    starting_balance = Decimal(str(config["risk"]["starting_nav_usdt"]))
    usdt = Currency.from_str("USDT")
    venue = Venue("BINANCE")
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level=str(config["validation"].get("log_level", "ERROR"))),
            run_analysis=True,
        ),
    )
    strategy = Candidate02Strategy(
        Candidate02StrategyConfig(
            instrument_ids=tuple(instrument.id for instrument in instruments),
            bar_types=tuple(bar_types),
            core_config_json=json.dumps(config["scenario"], sort_keys=True),
            risk_fraction=Decimal(str(config["risk"]["risk_fraction"])),
            entry_fee_rate=Decimal(execution["taker_fee_rate"]),
            stop_fee_rate=Decimal(execution["taker_fee_rate"]),
            entry_slippage_rate=Decimal(execution["entry_slippage_rate"]),
            stop_slippage_rate=Decimal(execution["stop_slippage_rate"]),
            market_impact_rate=Decimal(execution["market_impact_rate"]),
            funding_rate_allowance=Decimal(execution["funding_rate_allowance"]),
            trade_start_ns=_ns(evaluation_start),
            trade_end_ns=_ns(evaluation_end),
        ),
    )

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(starting_balance, usdt)],
            base_currency=usdt,
            default_leverage=Decimal(str(config["execution"]["simulation_leverage_capacity"])),
            reject_stop_orders=False,
            support_contingent_orders=True,
            use_reduce_only=True,
            bar_execution=True,
            bar_adaptive_high_low_ordering=True,
            trade_execution=True,
            allow_cash_borrowing=False,
            liquidation_enabled=True,
            liquidation_cancel_open_orders=True,
        )
        for instrument in instruments:
            engine.add_instrument(instrument)
        engine.add_data(all_bars, sort=True)
        engine.add_strategy(strategy)
        engine.run()

        orders = engine.trader.generate_orders_report()
        order_fills = engine.trader.generate_order_fills_report()
        fills = engine.trader.generate_fills_report()
        positions = engine.trader.generate_positions_report()
        account_report = engine.trader.generate_account_report(venue)
        orders.to_csv(output / "orders.csv", index=True)
        order_fills.to_csv(output / "order_fills.csv", index=True)
        fills.to_csv(output / "fills.csv", index=True)
        positions.to_csv(output / "positions.csv", index=True)
        account_report.to_csv(output / "account.csv", index=True)
        _write_records(output / "signals.jsonl", strategy.signal_records)
        _write_records(output / "risk_sizing.jsonl", strategy.sizing_records)
        _write_records(output / "trades.jsonl", strategy.trade_records)
        _write_records(output / "nav.jsonl", strategy.nav_records)

        events = _transitions_to_events(strategy.transition_records, scenario_prefix=label)
        write_events(output / "scenario_events.jsonl", events)
        account = engine.cache.account_for_venue(venue)
        if account is None:
            raise RuntimeError("backtest account missing after run")
        final_balance = account.balance_total(usdt)
        if final_balance is None:
            raise RuntimeError("USDT balance missing after run")
        final_nav = final_balance.as_double()
        flat_by_instrument = {
            str(instrument.id): engine.portfolio.is_flat(instrument.id)
            for instrument in instruments
        }
        if not all(flat_by_instrument.values()):
            raise RuntimeError(f"window ended with open position: {flat_by_instrument}")

        nav_values = [float(starting_balance)]
        nav_values.extend(float(item["nav"]) for item in strategy.nav_records if item["nav"] > 0)
        if not math.isclose(nav_values[-1], final_nav, rel_tol=1e-9, abs_tol=0.01):
            nav_values.append(final_nav)
        trade_pnls = [float(item["realized_pnl"]) for item in strategy.trade_records]
        wins = [value for value in trade_pnls if value > 0]
        losses = [value for value in trade_pnls if value < 0]
        commission_total = (
            float(sum(_parse_money(value) for value in fills.get("commission", [])))
            if not fills.empty
            else 0.0
        )
        planned_loss_ratios = []
        for item in strategy.sizing_records:
            budget = Decimal(str(item["risk_budget"]))
            planned = Decimal(str(item["planned_loss"]))
            if budget > 0:
                planned_loss_ratios.append(float(planned / budget))
        days = (evaluation_end - evaluation_start).total_seconds() / 86_400
        factor = final_nav / float(starting_balance)
        result_obj = engine.get_result()
        metrics = {
            "label": label,
            "symbols": list(symbols),
            "evaluation_start_utc": evaluation_start.isoformat(),
            "evaluation_end_utc": evaluation_end.isoformat(),
            "evaluation_days": days,
            "starting_nav_usdt": float(starting_balance),
            "final_nav_usdt": final_nav,
            "nav_factor": factor,
            "geometric_daily_growth": factor ** (1.0 / days) - 1.0,
            "max_drawdown_nav": max_drawdown(nav_values),
            "trades": len(trade_pnls),
            "trades_per_day": len(trade_pnls) / days,
            "wins": len(wins),
            "losses": len(losses),
            "flats": len(trade_pnls) - len(wins) - len(losses),
            "win_rate": len(wins) / len(trade_pnls) if trade_pnls else 0.0,
            "gross_profit_usdt": sum(wins),
            "gross_loss_usdt": sum(losses),
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else 0.0),
            "effective_commissions_usdt": commission_total,
            "orders": int(len(orders.index)),
            "fills": int(len(fills.index)),
            "positions_rows": int(len(positions.index)),
            "max_planned_loss_to_budget": max(planned_loss_ratios, default=0.0),
            "flat_at_end": flat_by_instrument,
            "runtime_diagnostics": strategy.diagnostic_snapshot(),
            "event_reason_counts": dict(Counter(event.reason_code for event in events)),
            "data_integrity": integrity,
            "engine_result": {
                "summary": _json_safe(result_obj.summary),
                "stats_pnls": _json_safe(result_obj.stats_pnls),
                "stats_returns": _json_safe(result_obj.stats_returns),
                "stats_general": _json_safe(result_obj.stats_general),
                "iterations": int(result_obj.iterations),
                "total_events": int(result_obj.total_events),
                "total_orders": int(result_obj.total_orders),
                "total_positions": int(result_obj.total_positions),
            },
            "execution_model": {
                **dict(execution),
                "effective_maker_commission_rate": str(effective_maker),
                "effective_taker_commission_rate": str(effective_taker),
                "bar_adaptive_high_low_ordering": True,
                "one_bar_cross_asset_arbitration_delay": True,
                "liquidation_enabled": True,
            },
        }
        write_json_atomic(output / "metrics.json", _json_safe(metrics))
        write_json_atomic(
            output / "data_manifest.json",
            {
                "dataset": "binance-vision-usdm-1m-klines",
                "window": label,
                "files": data_files,
                "integrity": integrity,
            },
        )
        return {
            "metrics": metrics,
            "events": events,
            "data_files": data_files,
            "output": str(output),
        }
    finally:
        engine.dispose()


def run_screen(
    *,
    config_path: Path,
    output: Path,
    cache_root: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    selection = config["validation"]["random_week_selection"]
    selected = deterministic_week_selection(
        seed=int(selection["seed"]),
        population_start=selection["population_start"],
        population_end=selection["population_end"],
        count=int(selection["count"]),
    )
    expected = [item["start"] for item in selection["locked_windows"]]
    if selected != expected:
        raise RuntimeError(f"locked windows are not reproducible: selected={selected}, expected={expected}")

    output.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        output / "selected_windows.json",
        {
            "seed": selection["seed"],
            "population_start": selection["population_start"],
            "population_end": selection["population_end"],
            "algorithm": "random.Random(seed).sample(all Monday starts, count)",
            "selected": selection["locked_windows"],
        },
    )
    results: list[dict[str, Any]] = []
    all_events: list[ResearchEvent] = []
    all_data_files: list[dict[str, Any]] = []
    for item in selection["locked_windows"]:
        start = _utc_midnight(item["start"])
        end = start + timedelta(days=7)
        result = run_window(
            label=item["role"],
            symbols=("BTCUSDT",),
            evaluation_start=start,
            evaluation_end=end,
            config=config,
            output=output / item["role"],
            cache_root=cache_root,
        )
        results.append(result["metrics"])
        all_events.extend(result["events"])
        all_data_files.extend(result["data_files"])

    all_events.sort(key=lambda event: (event.observed_time_ns, event.scenario_id))
    write_events(output / "scenario_events.jsonl", all_events)
    factors = [float(item["nav_factor"]) for item in results]
    total_days = sum(float(item["evaluation_days"]) for item in results)
    aggregate_growth = geometric_daily_growth(factors, total_days)
    total_trades = sum(int(item["trades"]) for item in results)
    total_wins = sum(int(item["wins"]) for item in results)
    total_losses = sum(int(item["losses"]) for item in results)
    aggregate = {
        "candidate": config["candidate"],
        "stage": "btc_random_week_screen",
        "locked_before_market_data": True,
        "windows": results,
        "total_evaluation_days": total_days,
        "combined_nav_factor": math.prod(factors),
        "geometric_daily_growth": aggregate_growth,
        "target_geometric_daily_growth": float(config["objective"]["geometric_daily_growth"]),
        "target_met": aggregate_growth >= float(config["objective"]["geometric_daily_growth"]),
        "total_trades": total_trades,
        "trades_per_day": total_trades / total_days,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate": total_wins / total_trades if total_trades else 0.0,
        "worst_window_growth": min(float(item["geometric_daily_growth"]) for item in results),
        "worst_window_drawdown": min(float(item["max_drawdown_nav"]) for item in results),
        "all_windows_positive": all(float(item["nav_factor"]) > 1.0 for item in results),
        "max_planned_loss_to_budget": max(
            float(item["max_planned_loss_to_budget"]) for item in results
        ),
        "one_global_entry_or_position_contract": True,
        "decision": "ADVANCE" if aggregate_growth >= float(config["objective"]["geometric_daily_growth"]) else "REJECT_OR_REDESIGN",
    }
    write_json_atomic(output / "metrics.json", _json_safe(aggregate))
    write_json_atomic(
        output / "data_manifest.json",
        {
            "dataset": "binance-vision-usdm-1m-klines",
            "files": all_data_files,
            "file_count": len(all_data_files),
        },
    )
    run_id = f"candidate-02-screen-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    write_json_atomic(
        output / "run.json",
        create_run_manifest(
            run_id=run_id,
            candidate=config["candidate"],
            config_path=config_path,
            data_manifest_path=output / "data_manifest.json",
            extra={
                "stage": "btc_random_week_screen",
                "selected_windows": selection["locked_windows"],
                "metrics_sha256": sha256_file(output / "metrics.json"),
                "scenario_events_sha256": sha256_file(output / "scenario_events.jsonl"),
            },
        ),
    )
    return aggregate
