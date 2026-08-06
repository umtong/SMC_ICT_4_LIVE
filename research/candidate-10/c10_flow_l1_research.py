"""Official L1-backed NautilusTrader runner for candidate 10.

This is an execution-infrastructure control, not a strategy change. Every
aggregate trade is paired only with the latest official Binance bookTicker
event already observable at that trade, then replayed in bounded batches
through NautilusTrader's streaming BacktestEngine API.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import time
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import build_data_manifest
from smc_ict_4.manifest import create_run_manifest
from smc_ict_4.manifest import write_data_manifest
from smc_ict_4.manifest import write_json_atomic

import c10_flow_research as _base_research
from c10_flow_model import FlowParams
from c10_flow_strategy import FlowCandidate10Config
from c10_l1_cache import _summarize_alignment
from c10_l1_cache import prepare_alignment_week
from c10_l1_data import download_binance_bookticker_week
from c10_l1_replay import _bars_by_open_day
from c10_l1_replay import chunk_events_by_timestamp
from c10_l1_replay import iter_day_events
from c10_model import NS_PER_MINUTE
from c10_research import _daily_metrics
from c10_research import _enrich_trades
from c10_research import _filled_execution_diagnostics
from c10_research import _write_csv
from c10_research import download_binance_week
from c10_research import load_bars
from c10_strategy import make_cost_loaded_btc_perpetual

def run_l1_flow_backtest(
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
    """Run the unchanged flow state machine with official causal L1 BBO state."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    data_root_path = Path(data_root)
    instrument = make_cost_loaded_btc_perpetual()

    started = time.perf_counter()
    kline_paths, kline_metadata = download_binance_week(
        week_start,
        data_root_path / "klines",
    )
    agg_paths, agg_metadata = _base_research.download_binance_aggtrade_week(
        week_start,
        data_root_path / "aggtrades",
    )
    book_paths, book_metadata = download_binance_bookticker_week(
        week_start,
        data_root_path / "bookticker",
    )
    bars, bar_quality = load_bars(kline_paths, instrument)
    if bar_quality["gap_count"]:
        raise RuntimeError(
            f"market data has {bar_quality['gap_count']} gaps; refusing to backtest",
        )
    cache_paths, cache_metadata_paths, alignment_reports = prepare_alignment_week(
        week_start=week_start,
        book_paths=book_paths,
        trade_paths=agg_paths,
        book_metadata=book_metadata,
        trade_metadata=agg_metadata,
        cache_directory=data_root_path / "l1-aligned",
    )
    alignment_quality = _summarize_alignment(alignment_reports)
    if (
        alignment_quality["future_quote_violation_count"]
        or alignment_quality["selected_nonpositive_spread_count"]
    ):
        raise RuntimeError(f"L1 causal gate failed: {alignment_quality}")

    # The official archives are checksum-verified. The derived fixed-size files
    # carry their own SHA-256 in the included small metadata manifests.
    data_manifest = build_data_manifest(
        data_root_path,
        dataset=f"binance-um-BTCUSDT-flow-l1-{week_start.isoformat()}",
        include=[
            *kline_paths,
            *agg_paths,
            *book_paths,
            *cache_metadata_paths,
        ],
        metadata_values={
            "klines": kline_metadata,
            "aggregate_trades": agg_metadata,
            "bookticker": book_metadata,
            "causal_alignment": alignment_quality,
        },
    )
    data_manifest_path = destination / "data_manifest.json"
    write_data_manifest(data_manifest_path, data_manifest)
    preparation_seconds = time.perf_counter() - started

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
    strategy_class = _base_research.FlowCandidate10Strategy
    strategy = strategy_class(
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
    run_id = f"candidate-10-v4-l1-{variant}-{week_start.isoformat()}"
    replay_state: dict[str, Any] = {
        "last_quote_id": None,
        "quote_events_emitted": 0,
        "trade_events_emitted": 0,
        "stream_batches": 0,
    }
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
        engine.add_strategy(strategy)
        bars_by_day = _bars_by_open_day(bars)

        engine_started = time.perf_counter()
        for cache_path, report in zip(cache_paths, alignment_reports, strict=True):
            day = str(report["date"])
            events = iter_day_events(
                cache_path=cache_path,
                bars=bars_by_day.get(day, []),
                instrument=instrument,
                replay_state=replay_state,
            )
            for batch in chunk_events_by_timestamp(events):
                engine.add_data(batch)
                engine.run(streaming=True)
                engine.clear_data()
                replay_state["stream_batches"] += 1
        engine.end()
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
        event_diagnostics = _base_research._event_diagnostics(strategy.events)
        flow_diagnostics = (
            strategy.flow_machine.diagnostics()
            if strategy.flow_machine is not None
            else {}
        )
        metrics = {
            "run_id": run_id,
            "candidate": "candidate-10",
            "candidate_generation": "v4-efficient-flow-acceptance-continuation",
            "execution_generation": (
                "official-l1-quote-plus-trade-stream-parent-protected"
            ),
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
            "aggregate_trade_data_quality": alignment_quality,
            "bookticker_data_quality": {
                "provider": book_metadata["provider"],
                "dataset": book_metadata["dataset"],
                "files": book_metadata["files"],
                "quote_events_emitted": replay_state["quote_events_emitted"],
                "known_limit": (
                    "bookTicker is top-of-book only; it has no full queue priority, "
                    "individual submissions/cancellations, or hidden liquidity"
                ),
            },
            "flow_diagnostics": flow_diagnostics,
            "event_diagnostics": event_diagnostics,
            "causal_gate_pass": (
                event_diagnostics["causality_violation_count"] == 0
                and alignment_quality["future_quote_violation_count"] == 0
            ),
            "performance_seconds": {
                "download_verify_align_manifest": preparation_seconds,
                "nautilus_streaming_replay": engine_seconds,
                "trade_events_per_replay_second": (
                    replay_state["trade_events_emitted"] / engine_seconds
                    if engine_seconds > 0.0
                    else None
                ),
                "quote_events_emitted": replay_state["quote_events_emitted"],
                "trade_events_emitted": replay_state["trade_events_emitted"],
                "stream_batches": replay_state["stream_batches"],
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
                    "official best bid/ask is present, but full queue priority and "
                    "hidden depth are unavailable; maker fee includes a two-basis-"
                    "point adverse-selection reserve"
                ),
                "funding": (
                    "positions and parents canceled/flattened before "
                    "00:00, 08:00, 16:00 UTC"
                ),
            },
            "risk": {
                "risk_fraction": str(risk_fraction),
                "sizing_basis": "current Nautilus portfolio equity",
                "planned_loss_components": (
                    "entry-to-stop distance + maker entry fee + taker stop fee "
                    "+ two ticks"
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
                    "engine": "NautilusTrader BacktestEngine streaming API",
                    "candidate_generation": metrics["candidate_generation"],
                    "execution_generation": metrics["execution_generation"],
                    "causal_gate_pass": metrics["causal_gate_pass"],
                    "aggregate_trade_data_quality": alignment_quality,
                    "bookticker_data_quality": metrics["bookticker_data_quality"],
                },
            ),
        )
        return metrics
    finally:
        engine.dispose()

__all__ = ["run_l1_flow_backtest"]
