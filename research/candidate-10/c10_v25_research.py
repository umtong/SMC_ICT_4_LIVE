"""Reproducible NautilusTrader runner for v25 liquidity-response auctions."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import time
from typing import Any, Iterable

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money

from smc_ict_4.event_log import write_events

import c10_liquidation_research as _base
import c10_v24_research as _v24
from c10_live_cost_ledger import _impact_event_rows
from c10_live_cost_math import impact_adjusted_ledger, live_ledger_diagnostics
from c10_v25_model import LiquidityResponseParams, NS_PER_MINUTE, NS_PER_SECOND
from c10_v25_strategy import (
    LiquidityResponseCandidate10Config,
    LiquidityResponseCandidate10Strategy,
)

BOOK_TICKER_ROOT = "https://data.binance.vision/data/futures/um/daily/bookTicker"
PERP_DAILY_ROOT = "https://data.binance.vision/data/futures/um/daily"


@dataclass(slots=True)
class _QuoteBucket:
    open_mid: float
    high_mid: float
    low_mid: float
    close_mid: float
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    spread_sum: float
    max_spread: float
    quote_updates: int
    ofi_qty: float
    bid_add_qty: float
    bid_remove_qty: float
    ask_add_qty: float
    ask_remove_qty: float
    first_event_ts_ns: int
    last_event_ts_ns: int

    def update(
        self,
        *,
        mid: float,
        bid_price: float,
        ask_price: float,
        bid_size: float,
        ask_size: float,
        spread: float,
        ofi_qty: float,
        bid_add_qty: float,
        bid_remove_qty: float,
        ask_add_qty: float,
        ask_remove_qty: float,
        event_ns: int,
    ) -> None:
        self.high_mid = max(self.high_mid, mid)
        self.low_mid = min(self.low_mid, mid)
        self.close_mid = mid
        self.bid_price = bid_price
        self.ask_price = ask_price
        self.bid_size = bid_size
        self.ask_size = ask_size
        self.spread_sum += spread
        self.max_spread = max(self.max_spread, spread)
        self.quote_updates += 1
        self.ofi_qty += ofi_qty
        self.bid_add_qty += bid_add_qty
        self.bid_remove_qty += bid_remove_qty
        self.ask_add_qty += ask_add_qty
        self.ask_remove_qty += ask_remove_qty
        self.last_event_ts_ns = event_ns


def reproducible_weeks(seed: int = 20260806) -> list[date]:
    return _base.reproducible_weeks(seed)


def download_liquidity_inputs(
    week_start: date,
    destination: str | Path,
    *,
    symbol: str = "BTCUSDT",
    warmup_days: int = 1,
) -> tuple[dict[str, list[Path]], dict[str, Any]]:
    root = Path(destination)
    paths: dict[str, list[Path]] = {
        "book_ticker": [],
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
        book_stem = f"{symbol}-bookTicker-{day}"
        book_url = f"{BOOK_TICKER_ROOT}/{symbol}/{book_stem}.zip"
        archive, checksum, record = _base._download_archive(
            url=book_url,
            destination=root / "book_ticker" / f"{book_stem}.zip",
        )
        paths["book_ticker"].extend([archive, checksum])
        metadata["datasets"]["book_ticker"].append(record)

        trade_stem = f"{symbol}-aggTrades-{day}"
        trade_url = f"{PERP_DAILY_ROOT}/aggTrades/{symbol}/{trade_stem}.zip"
        archive, checksum, record = _base._download_archive(
            url=trade_url,
            destination=root / "perp_aggtrades" / f"{trade_stem}.zip",
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
            archive, checksum, record = _base._download_archive(
                url=kline_url,
                destination=root / "perp_klines" / f"{kline_stem}.zip",
            )
            paths["perp_klines"].extend([archive, checksum])
            metadata["datasets"]["perp_klines"].append(record)
        cursor += timedelta(days=1)
    return paths, metadata


def _side_changes(
    *,
    previous_price: float,
    previous_size: float,
    price: float,
    size: float,
    is_bid: bool,
) -> tuple[float, float]:
    """Return best-level additions and removals without inventing deeper depth."""
    add = 0.0
    remove = 0.0
    if price == previous_price:
        change = size - previous_size
        if change >= 0.0:
            add = change
        else:
            remove = -change
    elif (is_bid and price > previous_price) or ((not is_bid) and price < previous_price):
        add = size
    else:
        remove = previous_size
    return max(0.0, add), max(0.0, remove)


def aggregate_book_ticker_archives(
    paths: Iterable[Path],
    *,
    bucket_seconds: int,
) -> tuple[dict[int, _QuoteBucket], dict[str, Any]]:
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    bucket_ns = bucket_seconds * NS_PER_SECOND
    buckets: dict[int, _QuoteBucket] = {}
    previous: tuple[int, float, float, float, float, int] | None = None
    row_count = 0
    boundary_duplicates = 0
    duplicate_update_ids = 0
    nonmonotonic = 0
    crossed_quotes = 0
    locked_quotes = 0
    schemas: Counter[int] = Counter()
    first_event_ns: int | None = None
    last_event_ns: int | None = None
    first_update_id: int | None = None
    last_update_id: int | None = None

    for path in sorted((item for item in paths if item.suffix == ".zip"), key=str):
        for raw in _base._iter_csv_rows(path):
            if not raw or not raw[0].lstrip("-").isdigit():
                continue
            if len(raw) != 7:
                raise RuntimeError(
                    f"unexpected bookTicker width {len(raw)} in {path}",
                )
            schemas[len(raw)] += 1
            update_id = int(raw[0])
            bid_price = float(raw[1])
            bid_size = float(raw[2])
            ask_price = float(raw[3])
            ask_size = float(raw[4])
            event_ns = _base._timestamp_ns(raw[6])
            if ask_price < bid_price:
                crossed_quotes += 1
                continue
            if ask_price == bid_price:
                locked_quotes += 1

            if previous is not None:
                previous_id, pbid, pbid_size, pask, pask_size, previous_ns = previous
                if update_id == previous_id:
                    if (
                        bid_price == pbid
                        and bid_size == pbid_size
                        and ask_price == pask
                        and ask_size == pask_size
                        and event_ns == previous_ns
                    ):
                        boundary_duplicates += 1
                        continue
                    duplicate_update_ids += 1
                    raise RuntimeError(
                        f"bookTicker duplicate update id with changed state: {update_id}",
                    )
                if event_ns < previous_ns:
                    nonmonotonic += 1
                    raise RuntimeError(
                        f"bookTicker event time moved backward: {event_ns} < {previous_ns}",
                    )
                ofi = (
                    (bid_size if bid_price >= pbid else 0.0)
                    - (pbid_size if bid_price <= pbid else 0.0)
                    - (ask_size if ask_price <= pask else 0.0)
                    + (pask_size if ask_price >= pask else 0.0)
                )
                bid_add, bid_remove = _side_changes(
                    previous_price=pbid,
                    previous_size=pbid_size,
                    price=bid_price,
                    size=bid_size,
                    is_bid=True,
                )
                ask_add, ask_remove = _side_changes(
                    previous_price=pask,
                    previous_size=pask_size,
                    price=ask_price,
                    size=ask_size,
                    is_bid=False,
                )
            else:
                ofi = 0.0
                bid_add = bid_remove = ask_add = ask_remove = 0.0

            mid = (bid_price + ask_price) / 2.0
            spread = ask_price - bid_price
            bucket_end = (event_ns // bucket_ns + 1) * bucket_ns
            bucket = buckets.get(bucket_end)
            if bucket is None:
                buckets[bucket_end] = _QuoteBucket(
                    open_mid=mid,
                    high_mid=mid,
                    low_mid=mid,
                    close_mid=mid,
                    bid_price=bid_price,
                    ask_price=ask_price,
                    bid_size=bid_size,
                    ask_size=ask_size,
                    spread_sum=spread,
                    max_spread=spread,
                    quote_updates=1,
                    ofi_qty=ofi,
                    bid_add_qty=bid_add,
                    bid_remove_qty=bid_remove,
                    ask_add_qty=ask_add,
                    ask_remove_qty=ask_remove,
                    first_event_ts_ns=event_ns,
                    last_event_ts_ns=event_ns,
                )
            else:
                bucket.update(
                    mid=mid,
                    bid_price=bid_price,
                    ask_price=ask_price,
                    bid_size=bid_size,
                    ask_size=ask_size,
                    spread=spread,
                    ofi_qty=ofi,
                    bid_add_qty=bid_add,
                    bid_remove_qty=bid_remove,
                    ask_add_qty=ask_add,
                    ask_remove_qty=ask_remove,
                    event_ns=event_ns,
                )
            previous = (
                update_id,
                bid_price,
                bid_size,
                ask_price,
                ask_size,
                event_ns,
            )
            row_count += 1
            first_event_ns = event_ns if first_event_ns is None else first_event_ns
            last_event_ns = event_ns
            first_update_id = update_id if first_update_id is None else first_update_id
            last_update_id = update_id

    if not buckets:
        raise RuntimeError("no bookTicker rows loaded")
    for end_ns, bucket in buckets.items():
        if bucket.last_event_ts_ns >= end_ns:
            raise RuntimeError(
                f"bookTicker bucket contains noncausal boundary event: {end_ns}",
            )
    return buckets, {
        "raw_row_count": row_count,
        "bucket_count": len(buckets),
        "first_event_ts_ns": first_event_ns,
        "last_event_ts_ns": last_event_ns,
        "first_bucket_end_ns": min(buckets),
        "last_bucket_end_ns": max(buckets),
        "first_update_id": first_update_id,
        "last_update_id": last_update_id,
        "schema_counts": dict(schemas),
        "boundary_duplicate_count": boundary_duplicates,
        "duplicate_update_id_count": duplicate_update_ids,
        "nonmonotonic_event_time_count": nonmonotonic,
        "crossed_quote_count": crossed_quotes,
        "locked_quote_count": locked_quotes,
        "timestamp_semantics": (
            "bookTicker event_time; completed [start,end) bucket; every source "
            "event timestamp is strictly earlier than bucket end"
        ),
        "ofi_definition": "Cont-Kukanov-Stoikov best-bid/ask event OFI",
    }


def align_liquidity_rows(
    quotes: dict[int, _QuoteBucket],
    trades: dict[int, Any],
    *,
    bucket_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not quotes:
        raise RuntimeError("quote buckets are empty")
    interval_ns = bucket_seconds * NS_PER_SECOND
    first = min(quotes)
    last = max(quotes)
    rows: list[dict[str, Any]] = []
    missing_quote_intervals = 0
    missing_trade_intervals = 0
    carried: _QuoteBucket | None = None
    ts_ns = first
    while ts_ns <= last:
        quote = quotes.get(ts_ns)
        if quote is not None:
            carried = quote
            mid_open = quote.open_mid
            mid_high = quote.high_mid
            mid_low = quote.low_mid
            mid_close = quote.close_mid
            mean_spread = quote.spread_sum / quote.quote_updates
            max_spread = quote.max_spread
            quote_updates = quote.quote_updates
            ofi_qty = quote.ofi_qty
            bid_add = quote.bid_add_qty
            bid_remove = quote.bid_remove_qty
            ask_add = quote.ask_add_qty
            ask_remove = quote.ask_remove_qty
        else:
            if carried is None:
                ts_ns += interval_ns
                continue
            missing_quote_intervals += 1
            mid_open = mid_high = mid_low = mid_close = carried.close_mid
            mean_spread = max_spread = carried.ask_price - carried.bid_price
            quote_updates = 0
            ofi_qty = bid_add = bid_remove = ask_add = ask_remove = 0.0

        trade = trades.get(ts_ns)
        if trade is None:
            missing_trade_intervals += 1
            trade_quote = taker_buy_quote = 0.0
            trade_base = taker_buy_base = 0.0
            trade_count = 0
        else:
            trade_quote = float(trade.quote_volume)
            taker_buy_quote = float(trade.taker_buy_quote)
            reference = max(1e-12, float(trade.close))
            trade_base = trade_quote / reference
            taker_buy_base = taker_buy_quote / reference
            trade_count = int(trade.trade_count)
        assert carried is not None
        rows.append(
            {
                "ts_ns": ts_ns,
                "mid_open": mid_open,
                "mid_high": mid_high,
                "mid_low": mid_low,
                "mid_close": mid_close,
                "bid_price": carried.bid_price,
                "ask_price": carried.ask_price,
                "bid_size": carried.bid_size,
                "ask_size": carried.ask_size,
                "mean_spread": mean_spread,
                "max_spread": max_spread,
                "quote_updates": quote_updates,
                "ofi_qty": ofi_qty,
                "bid_add_qty": bid_add,
                "bid_remove_qty": bid_remove,
                "ask_add_qty": ask_add,
                "ask_remove_qty": ask_remove,
                "trade_quote_volume": trade_quote,
                "taker_buy_quote": taker_buy_quote,
                "trade_base_volume": trade_base,
                "taker_buy_base": taker_buy_base,
                "trade_count": trade_count,
            },
        )
        ts_ns += interval_ns
    expected = (last - first) // interval_ns + 1
    if len(rows) != expected:
        raise RuntimeError(
            f"dense liquidity grid incomplete: {len(rows)} != {expected}",
        )
    return rows, {
        "aligned_row_count": len(rows),
        "expected_grid_row_count": expected,
        "first_ts_ns": first,
        "last_ts_ns": last,
        "bucket_seconds": bucket_seconds,
        "gap_count": 0,
        "missing_quote_interval_count": missing_quote_intervals,
        "missing_trade_interval_count": missing_trade_intervals,
        "causality": (
            "row timestamp is interval end; quote/trade source timestamps are "
            "strictly earlier; missing quote intervals carry only prior state "
            "and zero all order-flow changes"
        ),
    }


def run_liquidity_response_backtest(
    *,
    week_start: date,
    variant: str,
    params: LiquidityResponseParams,
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
    paths, source_metadata = download_liquidity_inputs(
        week_start,
        data_root_path,
    )
    quote_buckets, quote_quality = aggregate_book_ticker_archives(
        paths["book_ticker"],
        bucket_seconds=params.bar_seconds,
    )
    trade_buckets, aggregate_trade_quality = _v24.aggregate_aggtrade_archives(
        paths["perp_aggtrades"],
        bucket_seconds=params.bar_seconds,
        market="USD_M_PERPETUAL",
    )
    liquidity_rows, alignment_quality = align_liquidity_rows(
        quote_buckets,
        trade_buckets,
        bucket_seconds=params.bar_seconds,
    )
    bars, bar_quality = _v24.load_perp_nav_bars(
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
        _v24._deduplicated_manifest(paths, source_metadata),
    )

    bar_type = BarType.from_str(
        "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL",
    )
    strategy = LiquidityResponseCandidate10Strategy(
        LiquidityResponseCandidate10Config(
            instrument_id=instrument.id,
            bar_type=bar_type,
            eval_start_ns=eval_start_ns,
            eval_end_ns=eval_end_ns,
            risk_fraction=risk_fraction,
            params={
                name: getattr(params, name)
                for name in params.__dataclass_fields__
            },
            auction_rows=liquidity_rows,
            starting_balance=starting_balance,
        ),
    )
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    run_id = f"candidate-10-v25-{variant}-{week_start.isoformat()}"
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
            (float(row["impact_adjusted_net_pnl"]) for row in wins),
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
        machine_diag = (
            strategy.machine.diagnostics() if strategy.machine is not None else {}
        )
        ledger = live_ledger_diagnostics(
            trades=strategy.trade_records,
            risk_fraction=float(risk_fraction),
            adjusted_ending_nav=float(adjusted["impact_adjusted_ending_nav"]),
        )
        cluster_1 = _v24._event_cluster_diagnostics(
            strategy.trade_records,
            window_minutes=1,
        )
        cluster_5 = _v24._event_cluster_diagnostics(
            strategy.trade_records,
            window_minutes=5,
        )
        cluster_15 = _v24._event_cluster_diagnostics(
            strategy.trade_records,
            window_minutes=15,
        )
        causal_gate = bool(
            event_diag["causality_violation_count"] == 0
            and alignment_quality["gap_count"] == 0
            and quote_quality["duplicate_update_id_count"] == 0
            and quote_quality["nonmonotonic_event_time_count"] == 0
            and quote_quality["crossed_quote_count"] == 0
            and aggregate_trade_quality["duplicate_id_count"] == 0
            and aggregate_trade_quality["nonmonotonic_timestamp_count"] == 0
            and tick_quality["duplicate_id_count"] == 0
            and tick_quality["nonmonotonic_timestamp_count"] == 0
            and bar_quality["gap_count"] == 0
        )
        result: dict[str, Any] = {
            "run_id": run_id,
            "candidate": "candidate-10",
            "candidate_generation": "v25-liquidity-shelf-top-of-book-response",
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
                "book_ticker": quote_quality,
                "aggregate_trades": aggregate_trade_quality,
                "liquidity_alignment": alignment_quality,
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
                "stop_target_observation": (
                    "raw perpetual aggregate TradeTick last price"
                ),
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
                "remove only top-of-book OFI and attacked-side replenishment "
                "from failed-auction confirmation; shelf formation, price and "
                "executed trade flow, source-event clustering, target, entry, "
                "stop, expiry, fees, impact, seed and 3% current all-cost NAV "
                "risk remain identical"
            ),
            "causal_gate_pass": causal_gate,
            **{f"engine_{key}": value for key, value in raw_daily.items()},
        }
        result["target_pass"] = bool(
            result["impact_adjusted_geometric_daily_growth"] >= 0.01
            and result["closed_trades"] >= 7
            and result["wins"] >= 4
            and result["unique_source_event_count"] >= 7
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
                "quote_response_used": params.use_quote_response,
            },
        )
        return result
    finally:
        engine.dispose()


__all__ = [
    "aggregate_book_ticker_archives",
    "align_liquidity_rows",
    "download_liquidity_inputs",
    "reproducible_weeks",
    "run_liquidity_response_backtest",
]
