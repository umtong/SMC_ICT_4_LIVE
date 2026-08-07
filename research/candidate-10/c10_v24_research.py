"""Reproducible NautilusTrader runner for v24 spot-perpetual reconciliation."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any, Iterable

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from smc_ict_4.event_log import write_events

import c10_liquidation_research as _base
from c10_live_cost_ledger import _impact_event_rows
from c10_live_cost_math import impact_adjusted_ledger, live_ledger_diagnostics
from c10_v24_model import CrossMarketParams, NS_PER_MINUTE, NS_PER_SECOND
from c10_v24_strategy import (
    CrossMarketCandidate10Config,
    CrossMarketCandidate10Strategy,
)


SPOT_AGG_ROOT = "https://data.binance.vision/data/spot/daily/aggTrades"
PERP_DAILY_ROOT = "https://data.binance.vision/data/futures/um/daily"


@dataclass(slots=True)
class _AggBucket:
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    taker_buy_quote: float
    trade_count: int
    first_trade_ts_ns: int
    last_trade_ts_ns: int

    def update(
        self,
        *,
        price: float,
        notional: float,
        buyer_maker: bool,
        ts_ns: int,
    ) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.quote_volume += notional
        if not buyer_maker:
            self.taker_buy_quote += notional
        self.trade_count += 1
        self.last_trade_ts_ns = ts_ns


def reproducible_weeks(seed: int = 20260806) -> list[date]:
    return _base.reproducible_weeks(seed)


def _download_dataset(
    *,
    root: Path,
    dataset: str,
    url: str,
    stem: str,
) -> tuple[Path, Path, dict[str, Any]]:
    return _base._download_archive(
        url=url,
        destination=root / dataset / f"{stem}.zip",
    )


def download_cross_market_inputs(
    week_start: date,
    destination: str | Path,
    *,
    symbol: str = "BTCUSDT",
    warmup_days: int = 1,
) -> tuple[dict[str, list[Path]], dict[str, Any]]:
    """Download checksum-verified spot/perp trades and perp NAV bars."""

    root = Path(destination)
    paths: dict[str, list[Path]] = {
        "spot_aggtrades": [],
        "perp_aggtrades": [],
        "perp_execution_aggtrades": [],
        "perp_klines": [],
    }
    metadata: dict[str, Any] = {
        "provider": "Binance public data",
        "symbol": symbol,
        "week_start": week_start.isoformat(),
        "warmup_days": warmup_days,
        "datasets": {key: [] for key in paths},
    }
    first = week_start - timedelta(days=warmup_days)
    end = week_start + timedelta(days=7)
    cursor = first
    while cursor < end:
        day = cursor.isoformat()
        spot_stem = f"{symbol}-aggTrades-{day}"
        spot_url = f"{SPOT_AGG_ROOT}/{symbol}/{spot_stem}.zip"
        archive, checksum, record = _download_dataset(
            root=root,
            dataset="spot_aggtrades",
            url=spot_url,
            stem=spot_stem,
        )
        paths["spot_aggtrades"].extend([archive, checksum])
        metadata["datasets"]["spot_aggtrades"].append(record)

        perp_stem = f"{symbol}-aggTrades-{day}"
        perp_url = f"{PERP_DAILY_ROOT}/aggTrades/{symbol}/{perp_stem}.zip"
        archive, checksum, record = _download_dataset(
            root=root,
            dataset="perp_aggtrades",
            url=perp_url,
            stem=perp_stem,
        )
        paths["perp_aggtrades"].extend([archive, checksum])
        metadata["datasets"]["perp_aggtrades"].append(record)
        if cursor >= week_start:
            paths["perp_execution_aggtrades"].extend([archive, checksum])
            metadata["datasets"]["perp_execution_aggtrades"].append(record)

        if cursor >= week_start:
            kline_stem = f"{symbol}-5m-{day}"
            kline_url = (
                f"{PERP_DAILY_ROOT}/klines/{symbol}/5m/{kline_stem}.zip"
            )
            archive, checksum, record = _download_dataset(
                root=root,
                dataset="perp_klines",
                url=kline_url,
                stem=kline_stem,
            )
            paths["perp_klines"].extend([archive, checksum])
            metadata["datasets"]["perp_klines"].append(record)
        cursor += timedelta(days=1)
    return paths, metadata


def aggregate_aggtrade_archives(
    paths: Iterable[Path],
    *,
    bucket_seconds: int,
    market: str,
) -> tuple[dict[int, _AggBucket], dict[str, Any]]:
    """Stream aggregate raw venue trades into completed causal buckets."""

    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    bucket_ns = bucket_seconds * NS_PER_SECOND
    buckets: dict[int, _AggBucket] = {}
    previous_id: int | None = None
    previous_ts: int | None = None
    duplicate_ids = 0
    nonmonotonic = 0
    schemas: Counter[int] = Counter()
    row_count = 0
    buyer_notional = 0.0
    seller_notional = 0.0
    first_ts: int | None = None
    last_ts: int | None = None

    for path in sorted((item for item in paths if item.suffix == ".zip"), key=str):
        for raw in _base._iter_csv_rows(path):
            if not raw or not raw[0].lstrip("-").isdigit():
                continue
            if len(raw) not in {7, 8}:
                raise RuntimeError(
                    f"unexpected {market} aggTrade width {len(raw)} in {path}",
                )
            schemas[len(raw)] += 1
            agg_id = int(raw[0])
            price = float(raw[1])
            quantity = float(raw[2])
            ts_ns = _base._timestamp_ns(raw[5])
            buyer_maker = _base._bool_field(raw[6])
            if previous_id is not None and agg_id == previous_id:
                duplicate_ids += 1
            if previous_ts is not None and ts_ns < previous_ts:
                nonmonotonic += 1
            previous_id, previous_ts = agg_id, ts_ns
            first_ts = ts_ns if first_ts is None else first_ts
            last_ts = ts_ns
            row_count += 1
            notional = price * quantity
            if buyer_maker:
                seller_notional += notional
            else:
                buyer_notional += notional

            # [bucket_start, bucket_end): a trade exactly on a boundary belongs
            # to the next bucket, so every row is knowable strictly at bucket_end.
            bucket_end = (ts_ns // bucket_ns + 1) * bucket_ns
            existing = buckets.get(bucket_end)
            if existing is None:
                buckets[bucket_end] = _AggBucket(
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    quote_volume=notional,
                    taker_buy_quote=(0.0 if buyer_maker else notional),
                    trade_count=1,
                    first_trade_ts_ns=ts_ns,
                    last_trade_ts_ns=ts_ns,
                )
            else:
                existing.update(
                    price=price,
                    notional=notional,
                    buyer_maker=buyer_maker,
                    ts_ns=ts_ns,
                )

    if not buckets:
        raise RuntimeError(f"no {market} aggregate trades loaded")
    if duplicate_ids or nonmonotonic:
        raise RuntimeError(
            f"{market} aggregate-trade integrity failed: "
            f"duplicate={duplicate_ids}, nonmonotonic={nonmonotonic}",
        )
    for end_ns, bucket in buckets.items():
        if bucket.last_trade_ts_ns >= end_ns:
            raise RuntimeError(
                f"{market} bucket contains noncausal boundary trade: {end_ns}",
            )
    return buckets, {
        "market": market,
        "raw_row_count": row_count,
        "bucket_count": len(buckets),
        "first_trade_ts_ns": first_ts,
        "last_trade_ts_ns": last_ts,
        "first_bucket_end_ns": min(buckets),
        "last_bucket_end_ns": max(buckets),
        "schema_counts": dict(schemas),
        "duplicate_id_count": duplicate_ids,
        "nonmonotonic_timestamp_count": nonmonotonic,
        "buyer_aggressor_notional": buyer_notional,
        "seller_aggressor_notional": seller_notional,
        "timestamp_semantics": (
            "venue aggregate-trade transact_time; completed [start,end) bucket"
        ),
    }


def align_cross_market_rows(
    spot: dict[int, _AggBucket],
    perp: dict[int, _AggBucket],
    *,
    bucket_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    common = sorted(set(spot).intersection(perp))
    if not common:
        raise RuntimeError("spot and perpetual have no aligned completed buckets")
    interval_ns = bucket_seconds * NS_PER_SECOND
    gaps: list[dict[str, int]] = []
    previous: int | None = None
    rows: list[dict[str, Any]] = []
    for ts_ns in common:
        if previous is not None and ts_ns - previous != interval_ns:
            gaps.append({"previous_ts_ns": previous, "ts_ns": ts_ns})
        previous = ts_ns
        s = spot[ts_ns]
        p = perp[ts_ns]
        rows.append(
            {
                "ts_ns": ts_ns,
                "spot_open": s.open,
                "spot_high": s.high,
                "spot_low": s.low,
                "spot_close": s.close,
                "spot_quote_volume": s.quote_volume,
                "spot_taker_buy_quote": s.taker_buy_quote,
                "spot_trade_count": s.trade_count,
                "spot_last_trade_ts_ns": s.last_trade_ts_ns,
                "perp_open": p.open,
                "perp_high": p.high,
                "perp_low": p.low,
                "perp_close": p.close,
                "perp_quote_volume": p.quote_volume,
                "perp_taker_buy_quote": p.taker_buy_quote,
                "perp_trade_count": p.trade_count,
                "perp_last_trade_ts_ns": p.last_trade_ts_ns,
            },
        )
    return rows, {
        "aligned_row_count": len(rows),
        "spot_only_bucket_count": len(set(spot).difference(perp)),
        "perp_only_bucket_count": len(set(perp).difference(spot)),
        "gap_count": len(gaps),
        "gaps": gaps[:50],
        "first_ts_ns": common[0],
        "last_ts_ns": common[-1],
        "bucket_seconds": bucket_seconds,
        "causality": (
            "row timestamp is bucket end and each source last trade is strictly earlier"
        ),
    }


def load_perp_nav_bars(
    paths: Iterable[Path],
    instrument: Any,
    *,
    eval_start_ns: int,
    eval_end_ns: int,
) -> tuple[list[Bar], dict[str, Any]]:
    rows: dict[int, tuple[str, ...]] = {}
    for path in sorted((item for item in paths if item.suffix == ".zip"), key=str):
        for raw in _base._iter_csv_rows(path):
            if not raw or not raw[0].lstrip("-").isdigit():
                continue
            if len(raw) < 12:
                raise RuntimeError(f"short 5m kline row in {path}: {raw}")
            rows[int(raw[0])] = tuple(item.strip() for item in raw[:12])
    if not rows:
        raise RuntimeError("no perpetual 5m NAV bars loaded")

    bar_type = BarType.from_str(
        "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL",
    )
    bars: list[Bar] = []
    gaps: list[dict[str, int]] = []
    previous: int | None = None
    for open_ms, row in sorted(rows.items()):
        if previous is not None and open_ms - previous != 300_000:
            gaps.append({"previous_open_ms": previous, "open_ms": open_ms})
        previous = open_ms
        close_ns = (open_ms + 300_000) * 1_000_000
        if eval_start_ns <= close_ns < eval_end_ns:
            bars.append(
                Bar(
                    bar_type=bar_type,
                    open=Price(float(row[1]), precision=instrument.price_precision),
                    high=Price(float(row[2]), precision=instrument.price_precision),
                    low=Price(float(row[3]), precision=instrument.price_precision),
                    close=Price(float(row[4]), precision=instrument.price_precision),
                    volume=Quantity(
                        float(row[5]),
                        precision=instrument.size_precision,
                    ),
                    ts_event=close_ns,
                    ts_init=close_ns,
                ),
            )
    if gaps:
        raise RuntimeError(f"perpetual 5m data has {len(gaps)} gaps")
    if not bars:
        raise RuntimeError("evaluation NAV bars are empty")
    return bars, {
        "evaluation_bar_count": len(bars),
        "first_ts_ns": int(bars[0].ts_event),
        "last_ts_ns": int(bars[-1].ts_event),
        "gap_count": 0,
        "timestamp_semantics": "Binance 5m kline known at open_time+5m",
    }


def _deduplicated_manifest(
    paths: dict[str, list[Path]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dataset, items in paths.items():
        for path in items:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            files.append(
                {
                    "dataset": dataset,
                    "path": key,
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path.read_bytes()).hexdigest(),
                },
            )
    return {"metadata": metadata, "files": files}


def _event_cluster_diagnostics(
    trades: list[dict[str, Any]],
    *,
    window_minutes: int,
) -> dict[str, Any]:
    window_ns = window_minutes * NS_PER_MINUTE
    ordered = sorted(
        trades,
        key=lambda row: int(float(row.get("opened_ts_ns", 0) or 0)),
    )
    clusters: list[dict[str, Any]] = []
    for trade in ordered:
        ts_ns = int(float(trade.get("opened_ts_ns", 0) or 0))
        pnl = float(trade.get("impact_adjusted_net_pnl", 0.0) or 0.0)
        scenario_id = str(trade.get("scenario_id", ""))
        if not clusters or ts_ns - int(clusters[-1]["last_ts_ns"]) > window_ns:
            clusters.append(
                {
                    "first_ts_ns": ts_ns,
                    "last_ts_ns": ts_ns,
                    "pnl": pnl,
                    "scenario_ids": [scenario_id],
                },
            )
        else:
            clusters[-1]["last_ts_ns"] = ts_ns
            clusters[-1]["pnl"] = float(clusters[-1]["pnl"]) + pnl
            clusters[-1]["scenario_ids"].append(scenario_id)
    positive_total = sum(
        max(0.0, float(row.get("impact_adjusted_net_pnl", 0.0) or 0.0))
        for row in trades
    )
    largest_positive = max(
        (max(0.0, float(cluster["pnl"])) for cluster in clusters),
        default=0.0,
    )
    return {
        "window_minutes": window_minutes,
        "cluster_count": len(clusters),
        "largest_positive_cluster_pnl": largest_positive,
        "largest_positive_cluster_profit_concentration": (
            largest_positive / positive_total if positive_total > 0.0 else 0.0
        ),
        "clusters": clusters[:50],
    }


def run_cross_market_backtest(
    *,
    week_start: date,
    variant: str,
    params: CrossMarketParams,
    output_dir: str | Path,
    data_root: str | Path,
    starting_balance: Decimal = Decimal("100000"),
    risk_fraction: Decimal = Decimal("0.03"),
    seed: int = 20260806,
) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    data_root_path = Path(data_root)
    eval_start_ns = int(
        datetime.combine(
            week_start,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9,
    )
    eval_end_ns = eval_start_ns + 7 * 24 * 60 * NS_PER_MINUTE
    instrument = _base.make_cost_loaded_btc_perpetual()

    load_started = time.perf_counter()
    paths, source_metadata = download_cross_market_inputs(
        week_start,
        data_root_path,
    )
    spot_buckets, spot_quality = aggregate_aggtrade_archives(
        paths["spot_aggtrades"],
        bucket_seconds=params.bar_seconds,
        market="SPOT",
    )
    perp_buckets, perp_aggregate_quality = aggregate_aggtrade_archives(
        paths["perp_aggtrades"],
        bucket_seconds=params.bar_seconds,
        market="USD_M_PERPETUAL",
    )
    cross_rows, cross_quality = align_cross_market_rows(
        spot_buckets,
        perp_buckets,
        bucket_seconds=params.bar_seconds,
    )
    if cross_quality["gap_count"]:
        raise RuntimeError(
            "aligned spot/perpetual five-second rows are not continuous: "
            f"gap_count={cross_quality['gap_count']}",
        )
    bars, bar_quality = load_perp_nav_bars(
        paths["perp_klines"],
        instrument,
        eval_start_ns=eval_start_ns,
        eval_end_ns=eval_end_ns,
    )
    ticks, tick_quality = _base.load_aggtrade_ticks(
        paths["perp_execution_aggtrades"],
        instrument,
    )
    market_data = _base._merge_market_data(ticks, bars)
    load_seconds = time.perf_counter() - load_started
    _base.write_json_atomic(
        destination / "data_manifest.json",
        _deduplicated_manifest(paths, source_metadata),
    )

    bar_type = BarType.from_str(
        "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL",
    )
    strategy = CrossMarketCandidate10Strategy(
        CrossMarketCandidate10Config(
            instrument_id=instrument.id,
            bar_type=bar_type,
            eval_start_ns=eval_start_ns,
            eval_end_ns=eval_end_ns,
            risk_fraction=risk_fraction,
            params={
                name: getattr(params, name)
                for name in params.__dataclass_fields__
            },
            auction_rows=cross_rows,
            starting_balance=starting_balance,
        ),
    )
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    run_id = f"candidate-10-v24-{variant}-{week_start.isoformat()}"
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
            support_contingent_orders=False,
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
        write_events(destination / "scenario_events.jsonl", strategy.events)
        _base._write_csv(destination / "trades.csv", strategy.trade_records)
        _base._write_csv(destination / "equity_curve.csv", strategy.equity_curve)
        _base._write_csv(destination / "order_errors.csv", strategy.order_errors)

        raw_ending_nav = strategy._engine_equity()
        raw_daily = _base._daily_metrics(
            float(starting_balance),
            strategy.daily_nav,
        )
        adjusted = impact_adjusted_ledger(
            starting_nav=float(starting_balance),
            ending_nav=raw_ending_nav,
            daily_nav={
                str(item["date"]): float(item["nav"])
                for item in raw_daily["daily_nav"]
            },
            equity_curve=strategy.equity_curve,
            trades=strategy.trade_records,
            tick_max_drawdown=strategy.max_drawdown,
        )
        adjusted_curve = adjusted.pop("impact_adjusted_equity_curve")
        _base._write_csv(
            destination / "impact_adjusted_equity_curve.csv",
            adjusted_curve,
        )
        _base._write_csv(
            destination / "impact_cost_events.csv",
            _impact_event_rows(strategy.trade_records),
        )

        raw_wins = [
            row for row in strategy.trade_records
            if float(row.get("net_pnl", 0.0)) > 0.0
        ]
        raw_losses = [
            row for row in strategy.trade_records
            if float(row.get("net_pnl", 0.0)) < 0.0
        ]
        wins = [
            row for row in strategy.trade_records
            if float(row.get("impact_adjusted_net_pnl", 0.0)) > 0.0
        ]
        losses = [
            row for row in strategy.trade_records
            if float(row.get("impact_adjusted_net_pnl", 0.0)) < 0.0
        ]
        positive = sorted(
            (
                float(row["impact_adjusted_net_pnl"])
                for row in wins
            ),
            reverse=True,
        )
        concentration = (
            positive[0] / sum(positive)
            if positive and sum(positive) > 0.0
            else 0.0
        )
        scenario_pnl: dict[str, float] = defaultdict(float)
        for row in strategy.trade_records:
            scenario_pnl[str(row.get("scenario", "UNKNOWN"))] += float(
                row.get("impact_adjusted_net_pnl", 0.0),
            )
        event_diag = _base._event_diagnostics(strategy.events)
        machine_diag = strategy.machine.diagnostics() if strategy.machine is not None else {}
        ledger = live_ledger_diagnostics(
            trades=strategy.trade_records,
            risk_fraction=float(risk_fraction),
            adjusted_ending_nav=float(adjusted["impact_adjusted_ending_nav"]),
        )
        cluster_1 = _event_cluster_diagnostics(
            strategy.trade_records,
            window_minutes=1,
        )
        cluster_5 = _event_cluster_diagnostics(
            strategy.trade_records,
            window_minutes=5,
        )
        cluster_15 = _event_cluster_diagnostics(
            strategy.trade_records,
            window_minutes=15,
        )
        causal_gate = bool(
            event_diag["causality_violation_count"] == 0
            and cross_quality["gap_count"] == 0
            and spot_quality["duplicate_id_count"] == 0
            and spot_quality["nonmonotonic_timestamp_count"] == 0
            and perp_aggregate_quality["duplicate_id_count"] == 0
            and perp_aggregate_quality["nonmonotonic_timestamp_count"] == 0
            and tick_quality["duplicate_id_count"] == 0
            and tick_quality["nonmonotonic_timestamp_count"] == 0
            and bar_quality["gap_count"] == 0
        )
        result: dict[str, Any] = {
            "run_id": run_id,
            "candidate": "candidate-10",
            "candidate_generation": "v24-spot-perpetual-auction-reconciliation",
            "variant": variant,
            "week_start": week_start.isoformat(),
            "starting_nav": float(starting_balance),
            "ending_nav": raw_ending_nav,
            "net_return": raw_ending_nav / float(starting_balance) - 1.0,
            "intraday_max_drawdown": strategy.max_drawdown,
            **adjusted,
            "signals_seen": strategy.signals_seen,
            "signals_outside_evaluation": strategy.signals_outside_evaluation,
            "signals_blocked_by_open_risk": strategy.signals_blocked_by_open_risk,
            "plan_gap_rejections": strategy.plan_gap_rejections,
            "orders_submitted": strategy.orders_submitted,
            "closed_trades": len(strategy.trade_records),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (
                len(wins) / len(strategy.trade_records)
                if strategy.trade_records
                else 0.0
            ),
            "engine_wins": len(raw_wins),
            "engine_losses": len(raw_losses),
            "profit_concentration_largest_win": concentration,
            "scenario_net_pnl": dict(scenario_pnl),
            "win_loss_basis": "IMPACT_ADJUSTED_NET_PNL",
            "unique_source_event_count": len(
                {
                    str(row.get("source_pool_id", ""))
                    for row in strategy.trade_records
                },
            ),
            "event_cluster_diagnostics": {
                "one_minute": cluster_1,
                "five_minute": cluster_5,
                "fifteen_minute": cluster_15,
            },
            "forced_exits": strategy.forced_exits,
            "exit_requests": strategy.exit_requests,
            "order_error_count": len(strategy.order_errors),
            "state_diagnostics": machine_diag,
            "event_diagnostics": event_diag,
            "live_conservative_ledger": ledger,
            "data_quality": {
                "spot_aggregate_trades": spot_quality,
                "perpetual_aggregate_trades": perp_aggregate_quality,
                "cross_market_alignment": cross_quality,
                "execution_ticks": tick_quality,
                "nav_bars": bar_quality,
            },
            "runtime_seconds": {
                "data_load": load_seconds,
                "engine": engine_seconds,
            },
            "cost_model": {
                "maker_fee_plus_reserve": str(instrument.maker_fee),
                "taker_fee_plus_reserve": str(instrument.taker_fee),
                "fill_model_prob_slippage": 1.0,
                "impact": (
                    "causal size-dependent square-root participation model, "
                    "debited at actual entry/exit fills"
                ),
                "funding": "flat before 00:00, 08:00, 16:00 UTC windows",
                "native_stop_orders": False,
                "stop_target_observation": "raw perpetual aggregate TradeTick last price",
            },
            "risk": {
                "risk_fraction": str(risk_fraction),
                "sizing_basis": ledger["sizing_basis"],
                "arbitrary_notional_cap": False,
                "score_based_risk_multiplier": False,
            },
            "params": {
                name: getattr(params, name)
                for name in params.__dataclass_fields__
            },
            "exact_ablation": (
                "spot executed-flow observations are removed from detector and "
                "confirmation only; spot/perp prices, basis, perpetual flow, "
                "event identity, entry, stop, target, expiry, fees, impact, "
                "seed and 3% all-cost NAV risk are identical"
            ),
            "causal_gate_pass": causal_gate,
            **{
                f"engine_{key}": value
                for key, value in raw_daily.items()
            },
        }
        result["target_pass"] = bool(
            result["impact_adjusted_geometric_daily_growth"] >= 0.01
            and result["closed_trades"] >= 7
            and result["wins"] >= 4
            and result["profit_concentration_largest_win"] <= 0.50
            and cluster_15[
                "largest_positive_cluster_profit_concentration"
            ] <= 0.50
            and result["order_error_count"] == 0
            and result["impact_adjusted_intraday_max_drawdown"] < 0.30
            and result["causal_gate_pass"]
            and ledger["risk_budget_violation_count"] == 0
            and ledger["recorded_vs_reported_ending_nav_match"]
        )
        _base.write_json_atomic(destination / "metrics.json", result)
        _base.write_json_atomic(
            destination / "run.json",
            {
                "run_id": run_id,
                "candidate": "candidate-10",
                "variant": variant,
                "week_start": week_start.isoformat(),
                "engine": "NautilusTrader BacktestEngine 1.230.0",
                "seed": seed,
                "data_manifest": "data_manifest.json",
                "candidate_generation": result["candidate_generation"],
                "live_cost_ledger": True,
                "spot_flow_used": params.use_spot_flow,
            },
        )
        return result
    finally:
        engine.dispose()


__all__ = [
    "aggregate_aggtrade_archives",
    "align_cross_market_rows",
    "download_cross_market_inputs",
    "load_perp_nav_bars",
    "reproducible_weeks",
    "run_cross_market_backtest",
]
