"""Reproducible NautilusTrader research runner for candidate 10 v3."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import csv
import heapq
import io
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import zipfile

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
try:
    from nautilus_trader.model import TradeTick
except ImportError:  # pragma: no cover
    from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TradeId
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

from c10_flow_model import FlowParams
from c10_flow_strategy import FlowCandidate10Config
from c10_flow_strategy import FlowCandidate10Strategy
from c10_model import NS_PER_MINUTE
from c10_research import _daily_metrics
from c10_research import _enrich_trades
from c10_research import _filled_execution_diagnostics
from c10_research import _write_csv
from c10_research import download_binance_week
from c10_research import load_bars
from c10_research import reproducible_weeks
from c10_strategy import make_cost_loaded_btc_perpetual

AGGTRADE_ROOT = "https://data.binance.vision/data/futures/um/daily/aggTrades"
AGGTRADE_COLUMNS_7 = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)
AGGTRADE_COLUMNS_8 = AGGTRADE_COLUMNS_7 + ("is_best_match",)


def _download_bytes(url: str, attempts: int = 4) -> bytes:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(url, timeout=90) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {error}")


def download_binance_aggtrade_week(
    week_start: date,
    destination: str | Path,
    *,
    symbol: str = "BTCUSDT",
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
        stem = f"{symbol}-aggTrades-{cursor.isoformat()}"
        base = f"{AGGTRADE_ROOT}/{symbol}/{stem}.zip"
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
            {
                "date": cursor.isoformat(),
                "zip": zip_path.name,
                "sha256": actual,
                "archive_bytes": zip_path.stat().st_size,
            },
        )
        cursor += timedelta(days=1)
    return paths, {
        "provider": "Binance public data",
        "market": "USD-M futures",
        "dataset": "aggregate trades",
        "symbol": symbol,
        "week_start": week_start.isoformat(),
        "warmup_days": warmup_days,
        "files": records,
    }


def _timestamp_to_ns(raw: str) -> int:
    value = int(raw)
    if value < 10_000_000_000_000:  # milliseconds
        return value * 1_000_000
    if value < 10_000_000_000_000_000:  # microseconds
        return value * 1_000
    return value


def _bool_field(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"unexpected boolean field: {raw!r}")


def load_aggtrade_ticks(
    paths: Iterable[Path],
    instrument_id: Any,
) -> tuple[list[TradeTick], dict[str, Any]]:
    ticks: list[TradeTick] = []
    rows_read = 0
    buyer_rows = 0
    seller_rows = 0
    buyer_notional = 0.0
    seller_notional = 0.0
    duplicate_ids = 0
    nonmonotonic_timestamps = 0
    previous_id: int | None = None
    previous_ts: int | None = None
    first_id: int | None = None
    last_id: int | None = None
    first_ts: int | None = None
    last_ts: int | None = None
    schema_widths: Counter[int] = Counter()
    member_rows: dict[str, int] = {}

    for path in sorted((item for item in paths if item.suffix == ".zip"), key=str):
        local_rows = 0
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(members) != 1:
                raise RuntimeError(f"expected one CSV in {path}, found {members}")
            text = io.TextIOWrapper(archive.open(members[0]), encoding="utf-8")
            reader = csv.reader(text)
            for raw_row in reader:
                if not raw_row or not raw_row[0].lstrip("-").isdigit():
                    continue
                width = len(raw_row)
                if width not in {len(AGGTRADE_COLUMNS_7), len(AGGTRADE_COLUMNS_8)}:
                    raise RuntimeError(
                        f"unexpected aggregate-trade width {width} in {path}: {raw_row[:12]}",
                    )
                schema_widths[width] += 1
                row = [item.strip() for item in raw_row]
                agg_id = int(row[0])
                price_text = row[1]
                quantity_text = row[2]
                ts_ns = _timestamp_to_ns(row[5])
                buyer_maker = _bool_field(row[6])
                aggressor = (
                    AggressorSide.SELLER if buyer_maker else AggressorSide.BUYER
                )
                notional = float(price_text) * float(quantity_text)

                if previous_id is not None and agg_id == previous_id:
                    duplicate_ids += 1
                if previous_ts is not None and ts_ns < previous_ts:
                    nonmonotonic_timestamps += 1
                previous_id = agg_id
                previous_ts = ts_ns
                first_id = agg_id if first_id is None else first_id
                last_id = agg_id
                first_ts = ts_ns if first_ts is None else first_ts
                last_ts = ts_ns
                rows_read += 1
                local_rows += 1
                if aggressor == AggressorSide.BUYER:
                    buyer_rows += 1
                    buyer_notional += notional
                else:
                    seller_rows += 1
                    seller_notional += notional
                ticks.append(
                    TradeTick(
                        instrument_id=instrument_id,
                        price=Price.from_str(price_text),
                        size=Quantity.from_str(quantity_text),
                        aggressor_side=aggressor,
                        trade_id=TradeId(str(agg_id)),
                        ts_event=ts_ns,
                        ts_init=ts_ns,
                    ),
                )
        member_rows[path.name] = local_rows

    if not ticks:
        raise RuntimeError("no aggregate trades loaded")
    if duplicate_ids or nonmonotonic_timestamps:
        raise RuntimeError(
            "aggregate-trade integrity gate failed: "
            f"duplicate_ids={duplicate_ids}, nonmonotonic_ts={nonmonotonic_timestamps}",
        )
    return ticks, {
        "tick_count": len(ticks),
        "rows_read": rows_read,
        "schema_width_counts": dict(schema_widths),
        "member_rows": member_rows,
        "first_agg_trade_id": first_id,
        "last_agg_trade_id": last_id,
        "first_ts_ns": first_ts,
        "last_ts_ns": last_ts,
        "buyer_aggressor_rows": buyer_rows,
        "seller_aggressor_rows": seller_rows,
        "buyer_aggressor_notional": buyer_notional,
        "seller_aggressor_notional": seller_notional,
        "signed_aggressor_notional": buyer_notional - seller_notional,
        "duplicate_id_count": duplicate_ids,
        "nonmonotonic_timestamp_count": nonmonotonic_timestamps,
        "aggressor_mapping": {
            "is_buyer_maker=false": "BUYER",
            "is_buyer_maker=true": "SELLER",
        },
        "timestamp_semantics": "venue aggregate-trade transact_time",
    }


def _merge_market_data(
    ticks: list[TradeTick],
    bars: list[Bar],
) -> list[Any]:
    # Both inputs are already monotonic. Ticks at an exact bar-close timestamp
    # are dispatched first, then the completed one-minute NAV sampling bar.
    return list(
        heapq.merge(
            ticks,
            bars,
            key=lambda item: (
                int(item.ts_init),
                1 if isinstance(item, Bar) else 0,
            ),
        ),
    )


def _event_diagnostics(events: list[Any]) -> dict[str, Any]:
    event_types: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    causality_violations: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_types[str(event.event_type)] += 1
        reasons[str(event.reason_code)] += 1
        if int(event.observed_time_ns) < int(event.event_time_ns):
            causality_violations.append(
                {
                    "index": index,
                    "scenario_id": str(event.scenario_id),
                    "event_type": str(event.event_type),
                    "event_time_ns": int(event.event_time_ns),
                    "observed_time_ns": int(event.observed_time_ns),
                },
            )
    return {
        "event_type_counts": dict(event_types),
        "reason_counts": dict(reasons),
        "causality_violation_count": len(causality_violations),
        "causality_violations": causality_violations[:50],
    }


def run_flow_backtest(
    *,
    week_start: date,
    variant: str,
    params: FlowParams,
    output_dir: str | Path,
    data_root: str | Path,
    starting_balance: Decimal = Decimal("100000"),
    risk_fraction: Decimal = Decimal("0.03"),
    seed: int = 20260806,
) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    data_root_path = Path(data_root)
    instrument = make_cost_loaded_btc_perpetual()

    started = time.perf_counter()
    kline_paths, kline_metadata = download_binance_week(
        week_start,
        data_root_path / "klines",
    )
    agg_paths, agg_metadata = download_binance_aggtrade_week(
        week_start,
        data_root_path / "aggtrades",
    )
    downloaded = [*kline_paths, *agg_paths]
    data_manifest = build_data_manifest(
        data_root_path,
        dataset=f"binance-um-BTCUSDT-flow-v3-{week_start.isoformat()}",
        include=downloaded,
        metadata_values={
            "klines": kline_metadata,
            "aggregate_trades": agg_metadata,
        },
    )
    data_manifest_path = destination / "data_manifest.json"
    write_data_manifest(data_manifest_path, data_manifest)

    bars, bar_quality = load_bars(kline_paths, instrument)
    if bar_quality["gap_count"]:
        raise RuntimeError(
            f"market data has {bar_quality['gap_count']} gaps; refusing to backtest",
        )
    ticks, tick_quality = load_aggtrade_ticks(agg_paths, instrument.id)
    market_data = _merge_market_data(ticks, bars)
    data_load_seconds = time.perf_counter() - started

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
    strategy = FlowCandidate10Strategy(
        FlowCandidate10Config(
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
    run_id = f"candidate-10-v3-{variant}-{week_start.isoformat()}"
    try:
        usdt = Currency.from_str("USDT")
        engine.add_venue(
            venue=Venue("BINANCE"),
            oms_type=OmsType.NETTING,
            book_type=BookType.L1_MBP,
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
            trade_execution=True,
            bar_execution=False,
        )
        engine.add_instrument(instrument)
        engine.add_data(market_data)
        engine.add_strategy(strategy)
        engine_started = time.perf_counter()
        engine.run()
        engine_seconds = time.perf_counter() - engine_started

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(Venue("BINANCE"))
        fills.to_csv(destination / "orders.csv", index=False)
        positions.to_csv(destination / "positions.csv", index=False)
        account.to_csv(destination / "account.csv", index=False)

        _enrich_trades(
            strategy.trade_records,
            positions,
            bars,
            tick_size=instrument.price_increment.as_double(),
        )
        execution = _filled_execution_diagnostics(fills)
        write_events(destination / "scenario_events.jsonl", strategy.events)
        _write_csv(destination / "trades.csv", strategy.trade_records)
        _write_csv(destination / "equity_curve.csv", strategy.equity_curve)
        _write_csv(destination / "order_errors.csv", strategy.order_errors)

        end_equity = strategy._equity()
        net_pnl = end_equity - float(starting_balance)
        reported_commissions = float(execution["reported_commissions"])
        gross_price_pnl = net_pnl + reported_commissions
        daily = _daily_metrics(float(starting_balance), strategy.daily_nav)
        wins = [row for row in strategy.trade_records if row["net_pnl"] > 0]
        losses = [row for row in strategy.trade_records if row["net_pnl"] < 0]
        scenario_pnl: dict[str, float] = defaultdict(float)
        scenario_gross_pnl: dict[str, float] = defaultdict(float)
        exit_classes: Counter[str] = Counter()
        for row in strategy.trade_records:
            scenario = str(row["scenario"])
            scenario_pnl[scenario] += float(row["net_pnl"])
            scenario_gross_pnl[scenario] += float(
                row.get("gross_price_pnl_after_slippage", row["net_pnl"]),
            )
            exit_classes[str(row.get("exit_class", "UNKNOWN"))] += 1
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
        event_diagnostics = _event_diagnostics(strategy.events)
        flow_diagnostics = (
            strategy.flow_machine.diagnostics()
            if strategy.flow_machine is not None
            else {}
        )
        metrics = {
            "run_id": run_id,
            "candidate": "candidate-10",
            "candidate_generation": "v3-event-notional-flow-absorption-repricing",
            "execution_generation": "trade-tick-post-only-retrace-bracket",
            "variant": variant,
            "week_start": week_start.isoformat(),
            "starting_nav": float(starting_balance),
            "ending_nav": end_equity,
            "net_pnl": net_pnl,
            "net_return": end_equity / float(starting_balance) - 1.0,
            "gross_price_pnl_after_slippage_before_commissions": gross_price_pnl,
            "reported_commissions": reported_commissions,
            "commission_to_abs_gross_price_pnl": (
                reported_commissions / abs(gross_price_pnl)
                if gross_price_pnl
                else None
            ),
            "intraday_max_drawdown": strategy.max_drawdown,
            "signals_seen": strategy.signals_seen,
            "signals_outside_evaluation": strategy.signals_outside_evaluation,
            "signals_blocked_by_open_risk": strategy.signals_blocked_by_open_risk,
            "orders_submitted": strategy.orders_submitted,
            "pending_cancellations": strategy.pending_cancellations,
            "closed_trades": len(strategy.trade_records),
            "parent_fill_rate": (
                len(strategy.trade_records) / strategy.orders_submitted
                if strategy.orders_submitted
                else 0.0
            ),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (
                len(wins) / len(strategy.trade_records)
                if strategy.trade_records
                else 0.0
            ),
            "profit_concentration_largest_win": concentration,
            "scenario_net_pnl": dict(scenario_pnl),
            "scenario_gross_price_pnl_after_slippage": dict(scenario_gross_pnl),
            "exit_classes": dict(exit_classes),
            "execution": execution,
            "forced_exits": strategy.forced_exits,
            "order_error_count": len(strategy.order_errors),
            "bar_data_quality": bar_quality,
            "aggregate_trade_data_quality": tick_quality,
            "flow_diagnostics": flow_diagnostics,
            "event_diagnostics": event_diagnostics,
            "causal_gate_pass": event_diagnostics["causality_violation_count"] == 0,
            "performance_seconds": {
                "download_parse_merge": data_load_seconds,
                "nautilus_engine": engine_seconds,
                "ticks_per_engine_second": (
                    len(ticks) / engine_seconds if engine_seconds > 0.0 else None
                ),
            },
            "cost_model": {
                "entry": "post-only limit, maker fee plus adverse-selection reserve",
                "target": "post-only structural limit, maker fee plus reserve",
                "stop": "stop-market on raw trade ticks, taker fee plus reserve",
                "maker_fee_plus_execution_reserve": str(instrument.maker_fee),
                "taker_fee_plus_execution_reserve": str(instrument.taker_fee),
                "fill_model_prob_fill_on_limit": 1.0,
                "fill_model_prob_slippage": 1.0,
                "known_limit": (
                    "aggregate trades contain no queue position or hidden depth; "
                    "maker fee includes a two-basis-point adverse-selection reserve"
                ),
                "funding": (
                    "positions and parents canceled/flattened before 00:00, 08:00, 16:00 UTC"
                ),
            },
            "risk": {
                "risk_fraction": str(risk_fraction),
                "sizing_basis": "current Nautilus portfolio equity",
                "planned_loss_components": (
                    "entry-to-stop distance + maker entry fee + taker stop fee + two ticks"
                ),
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
            and metrics["causal_gate_pass"]
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
                    "candidate_generation": metrics["candidate_generation"],
                    "execution_generation": metrics["execution_generation"],
                    "causal_gate_pass": metrics["causal_gate_pass"],
                    "aggregate_trade_data_quality": tick_quality,
                },
            ),
        )
        return metrics
    finally:
        engine.dispose()


__all__ = [
    "download_binance_aggtrade_week",
    "load_aggtrade_ticks",
    "reproducible_weeks",
    "run_flow_backtest",
]
