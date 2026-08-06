"""Reproducible shared-account NautilusTrader runner for candidate-08 acceptance-only signals."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
import json
import shutil
from math import exp, isfinite, log
from pathlib import Path
import sys
from typing import Any, Mapping

import pandas as pd

from nautilus_trader.analysis.reporter import ReportProvider
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import LatencyModel, MakerTakerFeeModel, OneTickSlippageFillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model import FundingRateUpdate, MarkPriceUpdate
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_acceptance_evaluation import (  # noqa: E402
    fill_and_risk_contract_checks as _evaluate_risk_contracts,
    first_window_gate as _evaluate_first_window_gate,
    suite_summary as _evaluate_suite_summary,
)
from aggtrade_acceptance_funding import (  # noqa: E402
    FundingObservation,
    funding_observations_from_frame,
    load_official_funding_rates,
)
from aggtrade_acceptance_mark_price import load_official_mark_prices  # noqa: E402
from aggtrade_acceptance_signals import (  # noqa: E402
    AcceptanceLogicEvent,
    AcceptanceSignal,
    AcceptanceSignalBundle,
    build_acceptance_signals,
)
from aggtrade_acceptance_strategy import (  # noqa: E402
    AggTradeAcceptanceStrategy,
    AggTradeAcceptanceStrategyConfig,
)
from aggtrade_orderflow_probe import load_ten_second_aggtrades  # noqa: E402
from range_fvg_ltf_multiasset_probe import _load_frame  # noqa: E402
from range_fvg_logic import RangeFVGConfig  # noqa: E402
from run import (  # noqa: E402
    _equity_drawdown,
    _frame_for_analysis,
    _json_safe,
    _ns,
    _parse_utc,
    _position_metrics,
)
from smc_ict_4.contracts import ResearchEvent  # noqa: E402
from smc_ict_4.event_log import write_events  # noqa: E402
from smc_ict_4.manifest import create_run_manifest, sha256_file, write_json_atomic  # noqa: E402


VENUE = Venue("BINANCE")
USDT = Currency.from_str("USDT")


def _build_instrument(
    symbol: str,
    specification: Mapping[str, Any],
    fee_rate: float,
) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(str(specification["instrument_id"])),
        raw_symbol=Symbol(symbol),
        base_currency=Currency.from_str(str(specification["base_currency"])),
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=int(specification["price_precision"]),
        size_precision=int(specification["size_precision"]),
        price_increment=Price.from_str(str(specification["tick_size"])),
        size_increment=Quantity.from_str(str(specification["size_increment"])),
        max_quantity=None,
        min_quantity=Quantity.from_str(str(specification["min_quantity"])),
        max_notional=None,
        min_notional=Money(10.0, USDT),
        max_price=None,
        min_price=None,
        margin_init=Decimal("1.0"),
        margin_maint=Decimal("0.5"),
        maker_fee=Decimal(str(fee_rate)),
        taker_fee=Decimal(str(fee_rate)),
        ts_event=0,
        ts_init=0,
        info={
            "source": "candidate-08 fixed shared-account acceptance specification",
            "symbol": symbol,
            "fee_contains_execution_impact_reserve": True,
            "actual_funding_settled_separately": True,
        },
    )


def _create_engine(
    config: Mapping[str, Any],
    instruments: Mapping[str, CryptoPerpetual],
) -> BacktestEngine:
    latency = config["cost_assumptions"]["latency_ms"]
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
            run_analysis=True,
        )
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(float(config["starting_nav_usdt"]), USDT)],
        base_currency=USDT,
        default_leverage=Decimal(str(config["venue"]["default_leverage"])),
        fill_model=OneTickSlippageFillModel(
            prob_fill_on_limit=1.0,
            prob_slippage=float(config["cost_assumptions"]["one_tick_slippage_probability"]),
            random_seed=int(config["random_seed"]),
        ),
        fee_model=MakerTakerFeeModel(),
        latency_model=LatencyModel(
            base_latency_nanos=int(latency["base"] * 1_000_000),
            insert_latency_nanos=int(latency["insert"] * 1_000_000),
            update_latency_nanos=int(latency["update"] * 1_000_000),
            cancel_latency_nanos=int(latency["cancel"] * 1_000_000),
        ),
        reject_stop_orders=False,
        support_contingent_orders=True,
        use_position_ids=True,
        use_reduce_only=True,
        bar_execution=True,
        bar_adaptive_high_low_ordering=bool(
            config["venue"]["bar_adaptive_high_low_ordering"]
        ),
        liquidation_enabled=bool(config["venue"]["liquidation_enabled"]),
        liquidation_trigger_ratio=float(config["venue"]["liquidation_trigger_ratio"]),
        liquidation_cancel_open_orders=bool(
            config["venue"]["liquidation_cancel_open_orders"]
        ),
    )
    for instrument in instruments.values():
        engine.add_instrument(instrument)
    return engine


def _ten_second_bar_type(instrument: CryptoPerpetual) -> BarType:
    return BarType.from_str(f"{instrument.id}-10-SECOND-LAST-EXTERNAL")


def _bars_from_ten_second_frame(
    frame: pd.DataFrame,
    *,
    bar_type: BarType,
    instrument: CryptoPerpetual,
) -> list[Bar]:
    bars: list[Bar] = []
    for timestamp, row in frame.iterrows():
        values = [float(row[name]) for name in ("open", "high", "low", "close", "volume")]
        if not all(isfinite(value) for value in values):
            continue
        timestamp_ns = int(timestamp.as_unit("ns").value)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(values[0], instrument.price_precision),
                high=Price(values[1], instrument.price_precision),
                low=Price(values[2], instrument.price_precision),
                close=Price(values[3], instrument.price_precision),
                volume=Quantity(values[4], instrument.size_precision),
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
            )
        )
    return bars


def _funding_updates_from_frame(
    frame: pd.DataFrame,
    *,
    instrument: CryptoPerpetual,
) -> list[FundingRateUpdate]:
    updates: list[FundingRateUpdate] = []
    for timestamp, row in frame.iterrows():
        timestamp_ns = int(timestamp.as_unit("ns").value)
        updates.append(
            FundingRateUpdate(
                instrument_id=instrument.id,
                rate=Decimal(str(row["funding_rate"])),
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
                interval=int(row["funding_interval_minutes"]),
                next_funding_ns=None,
            )
        )
    return updates


def _mark_price_updates_from_frame(
    frame: pd.DataFrame,
    *,
    instrument: CryptoPerpetual,
) -> list[MarkPriceUpdate]:
    updates: list[MarkPriceUpdate] = []
    for timestamp, row in frame.iterrows():
        timestamp_ns = int(timestamp.as_unit("ns").value)
        updates.append(
            MarkPriceUpdate(
                instrument_id=instrument.id,
                value=instrument.make_price(Decimal(str(row["mark_price"]))),
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
            )
        )
    return updates



def _merge_signal_bundles(
    bundles: Mapping[str, AcceptanceSignalBundle],
    *,
    start_ns: int,
    end_ns: int,
) -> dict[int, tuple[AcceptanceSignal, ...]]:
    grouped: dict[int, list[AcceptanceSignal]] = {}
    for bundle in bundles.values():
        for timestamp, signals in bundle.signals_by_time_ns.items():
            if start_ns <= timestamp < end_ns:
                grouped.setdefault(timestamp, []).extend(signals)
    return {
        timestamp: tuple(
            sorted(
                signals,
                key=lambda signal: (
                    signal.net_reward_risk,
                    signal.boundary_source,
                    signal.target_source,
                    signal.symbol,
                ),
                reverse=True,
            )
        )
        for timestamp, signals in grouped.items()
    }


def _event_to_research(event: AcceptanceLogicEvent, sequence: int) -> tuple[ResearchEvent, int]:
    return (
        ResearchEvent(
            scenario_id=event.scenario_id,
            instrument_id=event.instrument_id,
            event_type=event.event_type,
            event_time_ns=event.event_time_ns,
            observed_time_ns=event.observed_time_ns,
            previous_state=event.previous_state,
            next_state=event.next_state,
            reason_code=event.reason_code,
            reference_price=(
                None if event.reference_price is None else format(event.reference_price, ".12g")
            ),
            details={"symbol": event.symbol, **event.details},
        ),
        sequence,
    )


def _write_merged_events(
    path: Path,
    *,
    signals_by_time_ns: Mapping[int, tuple[AcceptanceSignal, ...]],
    execution_events: list[dict[str, Any]],
) -> int:
    materialized: list[tuple[ResearchEvent, int]] = []
    for signals in signals_by_time_ns.values():
        for signal in signals:
            for sequence, event in zip((10, 20, 30), signal.events, strict=True):
                materialized.append(_event_to_research(event, sequence))
    for raw in execution_events:
        reference = raw.get("reference_price")
        materialized.append(
            (
                ResearchEvent(
                    scenario_id=str(raw["scenario_id"]),
                    instrument_id=str(raw["instrument_id"]),
                    event_type=str(raw["event_type"]),
                    event_time_ns=int(raw["event_time_ns"]),
                    observed_time_ns=int(raw["observed_time_ns"]),
                    previous_state=str(raw["previous_state"]),
                    next_state=str(raw["next_state"]),
                    reason_code=str(raw["reason_code"]),
                    reference_price=None if reference is None else format(float(reference), ".12g"),
                    details={"symbol": raw.get("symbol"), **dict(raw.get("details", {}))},
                ),
                int(raw.get("sequence", 75)),
            )
        )
    materialized.sort(
        key=lambda item: (
            item[0].observed_time_ns,
            item[0].scenario_id,
            item[1],
            item[0].event_type,
        )
    )
    write_events(path, [item[0] for item in materialized])
    return len(materialized)


def _global_signal_summary(
    signals_by_time_ns: Mapping[int, tuple[AcceptanceSignal, ...]],
) -> dict[str, Any]:
    signals = [signal for items in signals_by_time_ns.values() for signal in items]
    return {
        "signals": len(signals),
        "signal_times": len(signals_by_time_ns),
        "by_symbol": dict(sorted(Counter(signal.symbol for signal in signals).items())),
        "by_direction": dict(sorted(Counter(signal.direction_name for signal in signals).items())),
        "by_boundary_source": dict(
            sorted(Counter(signal.boundary_source for signal in signals).items())
        ),
        "by_target_source": dict(sorted(Counter(signal.target_source for signal in signals).items())),
        "median_raw_net_reward_risk": (
            float(pd.Series([signal.net_reward_risk for signal in signals]).median())
            if signals
            else 0.0
        ),
    }


def _closed_trade_records(
    enriched_positions: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    position_outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    intent_by_scenario = {str(item.get("scenario_id")): item for item in intents}
    outcome_by_position = {
        str(item.get("position_id")): item
        for item in position_outcomes
        if item.get("position_id") is not None
    }
    records: list[dict[str, Any]] = []
    for position in enriched_positions:
        scenario_id = str(position.get("scenario_id"))
        position_id = str(position.get("position_id"))
        intent = intent_by_scenario.get(scenario_id, {})
        outcome = outcome_by_position.get(position_id, {})
        records.append(
            {
                "scenario_id": scenario_id,
                "symbol": intent.get("symbol"),
                "instrument_id": intent.get("instrument_id"),
                "direction": intent.get("direction"),
                "position_id": position_id,
                "entry_fill_time_ns": intent.get("entry_fill_time_ns"),
                "position_open_time_ns": intent.get("position_open_time_ns"),
                "position_close_time_ns": outcome.get("ts_event"),
                "realized_pnl": float(position.get("realized_pnl_numeric", 0.0)),
                "close_reason": position.get("close_reason"),
                "risk_budget": intent.get("risk_budget"),
                "planned_stop_loss": intent.get("planned_stop_loss"),
            }
        )
    return records


def run_window(
    *,
    config: Mapping[str, Any],
    config_path: Path,
    pattern_config_path: Path,
    window: Mapping[str, Any],
    output_dir: Path,
    data_cache: Path,
    ablation: str = "none",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    start = _parse_utc(str(window["start"]))
    end = _parse_utc(str(window["end"]))
    if end <= start:
        raise ValueError(f"invalid evaluation window: {window}")
    replay_end = end + timedelta(minutes=int(config["maximum_hold_minutes"]) + 1)
    context_end = replay_end + timedelta(minutes=10)
    # The order-flow ratios use a shifted 180-bar median. A 35-minute pre-window replay
    # warms that state and consumes pre-window level interactions without allowing trades.
    aggtrade_start = start - timedelta(minutes=35)
    # The previous settled funding observation is required for a causal expected-cost reserve even
    # when the evaluation starts between settlement boundaries. The maximum published interval is
    # eight hours, so one full prior interval is sufficient without exposing any future rate.
    funding_context_start = start - timedelta(hours=8, minutes=5)

    fee_rate = float(config["effective_fee_rate_per_fill"])
    instruments = {
        symbol: _build_instrument(symbol, specification, fee_rate)
        for symbol, specification in config["assets"].items()
    }
    bar_types = {symbol: _ten_second_bar_type(instrument) for symbol, instrument in instruments.items()}
    pattern_source = json.loads(pattern_config_path.read_text(encoding="utf-8"))
    pattern = RangeFVGConfig.from_mapping(dict(pattern_source["pattern"]))

    bundles: dict[str, AcceptanceSignalBundle] = {}
    all_bars: list[Bar] = []
    all_funding_updates: list[FundingRateUpdate] = []
    all_mark_price_updates: list[MarkPriceUpdate] = []
    funding_observations_by_instrument: dict[str, tuple[FundingObservation, ...]] = {}
    manifests: dict[str, Any] = {}
    for symbol, instrument in instruments.items():
        kline_frame, kline_sources, kline_quality = _load_frame(
            symbol=symbol,
            # At least two complete UTC weeks must be visible before a Monday evaluation start;
            # ten days would silently omit the prior weekly high/low from the completed-level set.
            load_start=start - timedelta(days=15),
            load_end=context_end,
            cache_dir=data_cache / "klines",
        )
        ten_second, agg_sources, agg_quality = load_ten_second_aggtrades(
            symbol=symbol,
            start=aggtrade_start,
            end=replay_end,
            cache_dir=data_cache / "aggTrades",
        )
        funding = load_official_funding_rates(
            symbol=symbol,
            start=funding_context_start,
            end=replay_end,
            cache_dir=data_cache / "fundingRate",
        )
        funding_observations = funding_observations_from_frame(funding.frame)
        funding_observations_by_instrument[str(instrument.id)] = funding_observations
        replay_funding_frame = funding.frame.loc[
            (funding.frame.index >= aggtrade_start)
            & (funding.frame.index < replay_end)
        ].copy()
        funding_updates = _funding_updates_from_frame(
            replay_funding_frame,
            instrument=instrument,
        )
        all_funding_updates.extend(funding_updates)
        mark_prices = load_official_mark_prices(
            symbol=symbol,
            start=aggtrade_start,
            end=replay_end,
            cache_dir=data_cache / "markPriceKlines",
        )
        mark_price_updates = _mark_price_updates_from_frame(
            mark_prices.frame,
            instrument=instrument,
        )
        all_mark_price_updates.extend(mark_price_updates)

        # Build completed five-minute context and completed external levels through the existing
        # causal detector primitives; no future outcomes enter the signal schedule.
        from aggtrade_orderflow_probe import _context  # local import keeps runner dependencies explicit

        context_times, context_bars, snapshots = _context(kline_frame, pattern)
        bundle = build_acceptance_signals(
            data=ten_second,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
            symbol=symbol,
            instrument_id=str(instrument.id),
            tick=float(instrument.price_increment.as_double()),
            fee_rate=fee_rate,
            minimum_net_reward_risk=float(config["minimum_net_reward_risk"]),
            require_retest_contraction=ablation != "remove_retest_contraction",
        )
        bundles[symbol] = bundle
        bars = _bars_from_ten_second_frame(
            ten_second,
            bar_type=bar_types[symbol],
            instrument=instrument,
        )
        all_bars.extend(bars)
        manifests[symbol] = {
            "instrument_id": str(instrument.id),
            "bar_type": str(bar_types[symbol]),
            "ten_second_bars": len(bars),
            "aggtrade_quality": agg_quality,
            "aggtrade_files": [asdict(source) for source in agg_sources],
            "kline_quality": kline_quality,
            "kline_files": [asdict(source) for source in kline_sources],
            "funding_updates": len(funding_updates),
            "funding_causal_observations": len(funding_observations),
            "funding_context_start": funding_context_start.isoformat(),
            "mark_price": "latest completed one-minute mark close is available before each funding boundary and liquidation check",
            "funding_quality": funding.quality,
            "funding_files": [asdict(source) for source in funding.source_files],
            "mark_price_updates": len(mark_price_updates),
            "mark_price_quality": mark_prices.quality,
            "mark_price_files": [asdict(source) for source in mark_prices.source_files],
            "detector_diagnostics": bundle.diagnostics,
            "detector_rejections": len(bundle.rejected_scenarios),
        }

    signals_by_time_ns = _merge_signal_bundles(
        bundles,
        start_ns=_ns(start),
        end_ns=_ns(end),
    )
    data_manifest = {
        "candidate": config["candidate"],
        "window": dict(window),
        "ablation": ablation,
        "sources": {
            "order_flow": "checksum-verified Binance Vision USD-M daily aggTrades",
            "external_liquidity": "checksum-verified Binance Vision USD-M monthly one-minute klines",
            "funding": "checksum-verified Binance Vision USD-M monthly fundingRate settlements",
            "mark_price": "checksum-verified Binance Vision USD-M monthly one-minute markPriceKlines",
        },
        "timestamp_contract": {
            "ten_second_bar": "bucket-end timestamp; signal unavailable before completed bucket",
            "ten_second_warmup_start": aggtrade_start.isoformat(),
            "five_minute_context": "five completed one-minute source-close bars",
            "external_levels": "completed 4-hour/day/week periods only",
            "funding": "actual archive calc_time with interval converted from hours to minutes; sizing uses only the latest observation at or before each signal",
            "mark_price": "completed one-minute mark-price close stamped before the exact funding boundary; used by NautilusTrader funding settlement and liquidation",
            "funding_context_start": funding_context_start.isoformat(),
        },
        "assets": manifests,
    }
    data_manifest_path = output_dir / "data_manifest.json"
    write_json_atomic(data_manifest_path, _json_safe(data_manifest))

    strategy = AggTradeAcceptanceStrategy(
        AggTradeAcceptanceStrategyConfig(
            trading_start_ns=_ns(start),
            trading_end_ns=_ns(end),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            effective_fee_rate=Decimal(str(config["effective_fee_rate_per_fill"])),
            minimum_net_reward_risk=Decimal(str(config["minimum_net_reward_risk"])),
            maximum_hold_minutes=int(config["maximum_hold_minutes"]),
            funding_avoidance_minutes=int(config["funding_avoidance_minutes"]),
        ),
        instrument_ids=tuple(instrument.id for instrument in instruments.values()),
        bar_types=tuple(bar_types.values()),
        signals_by_time_ns=signals_by_time_ns,
        funding_observations_by_instrument=funding_observations_by_instrument,
    )

    all_bars.sort(key=lambda bar: (int(bar.ts_event), str(bar.bar_type.instrument_id)))
    all_funding_updates.sort(key=lambda item: (int(item.ts_event), str(item.instrument_id)))
    all_mark_price_updates.sort(key=lambda item: (int(item.ts_event), str(item.instrument_id)))
    engine = _create_engine(config, instruments)
    try:
        # Keep each homogeneous Nautilus data type in a separate add_data call, then perform one
        # deterministic global sort across bars and funding updates.
        engine.add_data(all_bars, sort=False)
        engine.add_data(all_mark_price_updates, sort=False)
        engine.add_data(all_funding_updates, sort=False)
        engine.sort_data()
        engine.add_strategy(strategy)
        engine.run()

        account = engine.cache.account_for_venue(VENUE)
        if account is None:
            raise RuntimeError("NautilusTrader did not retain the shared Binance margin account")
        cached_orders = engine.cache.orders()
        cached_positions = engine.cache.positions()
        orders = ReportProvider.generate_orders_report(cached_orders)
        fills = ReportProvider.generate_fills_report(cached_orders)
        positions = ReportProvider.generate_positions_report(cached_positions)
        account_report = ReportProvider.generate_account_report(account)
        orders.to_csv(output_dir / "orders.csv", index=True)
        fills.to_csv(output_dir / "fills.csv", index=True)
        positions.to_csv(output_dir / "positions.csv", index=True)
        account_report.to_csv(output_dir / "account.csv", index=True)

        final_money = account.balance_total(USDT)
        if final_money is None:
            raise RuntimeError("shared account had no total USDT balance")
        starting_nav = float(config["starting_nav_usdt"])
        final_nav = float(final_money.as_double())
        days = (end - start).total_seconds() / 86_400.0
        geometric = -1.0 if final_nav <= 0 else exp((log(final_nav) - log(starting_nav)) / days) - 1.0

        position_metrics, enriched_positions = _position_metrics(
            positions,
            strategy.trade_intents,
            strategy.position_outcomes,
        )
        closed_trade_records = _closed_trade_records(
            enriched_positions,
            strategy.trade_intents,
            strategy.position_outcomes,
        )
        max_drawdown, equity_curve = _equity_drawdown(account_report, starting_nav)
        open_positions = sum(
            len(engine.cache.positions_open(instrument_id=instrument.id))
            for instrument in instruments.values()
        )
        open_orders = sum(
            len(engine.cache.orders_open(instrument_id=instrument.id))
            for instrument in instruments.values()
        )
        unprocessed = sorted(set(signals_by_time_ns) - strategy.processed_signal_times)
        contract_checks = _evaluate_risk_contracts(
            strategy.trade_intents,
            closed_trade_records,
        )
        result = engine.get_result()
        metrics: dict[str, Any] = {
            "candidate": config["candidate"],
            "window": dict(window),
            "ablation": ablation,
            "calendar_days": days,
            "starting_nav_usdt": starting_nav,
            "final_nav_usdt": final_nav,
            "nav_multiple": final_nav / starting_nav,
            "total_return": final_nav / starting_nav - 1.0,
            "daily_geometric_growth": geometric,
            "goal_daily_geometric_growth": 0.01,
            "goal_met_in_window": geometric >= 0.01,
            "maximum_realized_equity_drawdown": max_drawdown,
            "detector": _global_signal_summary(signals_by_time_ns),
            "orders": int(len(orders.index)),
            "fills": int(len(fills.index)),
            "open_positions_after_run": open_positions,
            "open_orders_after_run": open_orders,
            "trade_intents": len(strategy.trade_intents),
            "skipped_setups": len(strategy.skipped_setups),
            "execution_failures": len(strategy.execution_failures),
            "unexpected_or_liquidation_closes": sum(
                item.get("close_reason") == "UNEXPECTED_CLOSE_OR_LIQUIDATION"
                for item in strategy.position_outcomes
            ),
            "position_metrics": position_metrics,
            "closed_trade_records": closed_trade_records,
            "contract_checks": contract_checks,
            "unprocessed_signal_times": len(unprocessed),
            "unprocessed_signal_time_ns": unprocessed,
            "nautilus_result": {
                "iterations": result.iterations,
                "total_events": result.total_events,
                "total_orders": result.total_orders,
                "total_positions": result.total_positions,
                "stats_pnls": result.stats_pnls,
                "stats_returns": result.stats_returns,
                "stats_general": getattr(result, "stats_general", {}),
            },
            "cost_assumptions": config["cost_assumptions"],
            "venue_runtime": {
                "default_leverage": config["venue"]["default_leverage"],
                "liquidation_enabled": config["venue"]["liquidation_enabled"],
                "liquidation_trigger_ratio": config["venue"]["liquidation_trigger_ratio"],
                "liquidation_cancel_open_orders": config["venue"][
                    "liquidation_cancel_open_orders"
                ],
            },
            "funding": {
                "actual_settlements_included": True,
                "causal_expected_cost_reserved_in_sizing": True,
                "expected_reserve_total_at_signal": sum(
                    float(item.get("quantity", 0.0))
                    * float(item.get("expected_funding_reserve_per_unit", 0.0))
                    for item in strategy.trade_intents
                ),
                "updates": len(all_funding_updates),
                "mark_price_updates": len(all_mark_price_updates),
                "settlement_price_source": "native MarkPriceUpdate from completed Binance markPriceKlines",
                "by_symbol": {
                    symbol: int(manifests[symbol]["funding_updates"])
                    for symbol in sorted(manifests)
                },
            },
        }
        gate_checks = _evaluate_first_window_gate(config, metrics)
        metrics["first_window_gate_checks"] = gate_checks
        metrics["first_window_gate_passed"] = all(gate_checks.values())

        write_json_atomic(output_dir / "metrics.json", _json_safe(metrics))
        write_json_atomic(
            output_dir / "signals.json",
            {
                "signals": _json_safe(
                    [
                        asdict(signal)
                        for timestamp in sorted(signals_by_time_ns)
                        for signal in signals_by_time_ns[timestamp]
                    ]
                )
            },
        )
        write_json_atomic(
            output_dir / "detector_rejections.json",
            {
                "by_symbol": {
                    symbol: _json_safe(list(bundle.rejected_scenarios))
                    for symbol, bundle in bundles.items()
                }
            },
        )
        write_json_atomic(
            output_dir / "trade_intents.json",
            {"trade_intents": _json_safe(strategy.trade_intents)},
        )
        write_json_atomic(
            output_dir / "position_outcomes.json",
            {
                "strategy_callbacks": _json_safe(strategy.position_outcomes),
                "enriched_positions": _json_safe(enriched_positions),
            },
        )
        write_json_atomic(
            output_dir / "skipped_setups.json",
            {"skipped_setups": _json_safe(strategy.skipped_setups)},
        )
        write_json_atomic(
            output_dir / "execution_failures.json",
            {"execution_failures": _json_safe(strategy.execution_failures)},
        )
        write_json_atomic(output_dir / "equity_curve.json", {"points": equity_curve})
        event_count = _write_merged_events(
            output_dir / "scenario_events.jsonl",
            signals_by_time_ns=signals_by_time_ns,
            execution_events=strategy.execution_events,
        )
        metrics["scenario_event_count"] = event_count
        write_json_atomic(output_dir / "metrics.json", _json_safe(metrics))

        run_manifest = create_run_manifest(
            run_id=f"candidate-08-aggtrade-acceptance-nautilus-{window['name']}",
            candidate=str(config["candidate"]),
            config_path=config_path,
            data_manifest_path=data_manifest_path,
            extra={
                "window": dict(window),
                "ablation": ablation,
                "pattern_config_path": str(pattern_config_path),
                "pattern_config_sha256": sha256_file(pattern_config_path),
                "engine": {
                    "name": "NautilusTrader BacktestEngine",
                    "shared_margin_account": True,
                    "global_entry_or_position_limit": 1,
                    "entry_order_type": "MARKET",
                    "exit_contingency": "OUO",
                    "instruments": [str(instrument.id) for instrument in instruments.values()],
                    "actual_funding_rate_updates": len(all_funding_updates),
                    "mark_price_updates": len(all_mark_price_updates),
                    "actual_funding_settlement_enabled": True,
                    "causal_funding_reserve_in_sizing": True,
                    "liquidation_enabled": config["venue"]["liquidation_enabled"],
                    "liquidation_trigger_ratio": config["venue"][
                        "liquidation_trigger_ratio"
                    ],
                },
                "result_summary": {
                    "final_nav_usdt": final_nav,
                    "daily_geometric_growth": geometric,
                    "closed_trades": position_metrics["closed_trades"],
                    "first_window_gate_passed": metrics["first_window_gate_passed"],
                },
            },
        )
        write_json_atomic(output_dir / "run.json", _json_safe(run_manifest))

        if open_positions or open_orders:
            raise RuntimeError(
                f"run ended with residual exposure: positions={open_positions}, orders={open_orders}"
            )
        if contract_checks["entry_fill_before_signal_count"]:
            raise RuntimeError("entry causality contract failed")
        if contract_checks["nonpositive_position_holding_time_count"]:
            raise RuntimeError("position closed at or before its entry fill timestamp")
        if contract_checks["missing_position_close_time_count"]:
            raise RuntimeError("closed position lacked a strategy close callback timestamp")
        if contract_checks["planned_loss_over_budget_count"]:
            raise RuntimeError("planned loss exceeded the 3% shared-NAV budget")
        if contract_checks["fill_adjusted_loss_over_budget_count"]:
            raise RuntimeError("fill-adjusted expected stop loss exceeded the 3% shared-NAV budget")
        if contract_checks["realized_loss_over_budget_count"]:
            raise RuntimeError("realized loss exceeded the signal-time 3% shared-NAV budget")
        if contract_checks["missing_funding_cost_state_count"]:
            raise RuntimeError("submitted entry lacked a causal funding-cost state")
        if contract_checks["funding_observation_after_signal_count"]:
            raise RuntimeError("funding-cost sizing used a future observation")
        if contract_checks["invalid_funding_reserve_count"]:
            raise RuntimeError("funding-cost reserve contract was invalid")
        if unprocessed:
            raise RuntimeError(f"signal timestamps were not processed: {unprocessed[:10]}")
        return metrics
    finally:
        engine.dispose()


def _suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    return _evaluate_suite_summary(config, suite, results)


def run_suite(
    *,
    config_path: Path,
    pattern_config_path: Path,
    suite: str,
    output: Path,
    data_cache: Path,
    reuse_first_dir: Path | None = None,
    ablation: str = "none",
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    windows = list(config["suites"][suite])
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for window in windows:
        destination = output / str(window["name"])
        if (
            suite == "screen"
            and str(window["name"]) == "screen-01"
            and reuse_first_dir is not None
            and ablation == "none"
        ):
            source = reuse_first_dir / "screen-01"
            metrics_path = source / "metrics.json"
            if not metrics_path.exists():
                raise FileNotFoundError(f"reused first-window metrics not found: {metrics_path}")
            reused = json.loads(metrics_path.read_text(encoding="utf-8"))
            if dict(reused.get("window", {})) != dict(window):
                raise RuntimeError("reused first-window evidence does not match the fixed screen-01 window")
            shutil.copytree(source, destination, dirs_exist_ok=True)
            result = reused
        else:
            result = run_window(
                config=config,
                config_path=config_path,
                pattern_config_path=pattern_config_path,
                window=window,
                output_dir=destination,
                data_cache=data_cache,
                ablation=ablation,
            )
        results.append(result)
        print(
            json.dumps(
                {
                    "window": window["name"],
                    "signals": result["detector"]["signals"],
                    "closed_trades": result["position_metrics"]["closed_trades"],
                    "wins": result["position_metrics"]["wins"],
                    "final_nav_usdt": result["final_nav_usdt"],
                    "daily_geometric_growth": result["daily_geometric_growth"],
                    "first_window_gate_passed": result["first_window_gate_passed"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
    summary = _suite_summary(config, suite, results)
    write_json_atomic(output / "suite_metrics.json", _json_safe(summary))
    write_json_atomic(
        output / "run.json",
        _json_safe(
            create_run_manifest(
                run_id=f"candidate-08-aggtrade-acceptance-nautilus-{suite}",
                candidate=str(config["candidate"]),
                config_path=config_path,
                extra={
                    "suite": suite,
                    "config_sha256": sha256_file(config_path),
                    "pattern_config_sha256": sha256_file(pattern_config_path),
                    "reused_first_window_evidence": (
                        str(reuse_first_dir) if suite == "screen" and reuse_first_dir else None
                    ),
                    "ablation": ablation,
                    "summary": summary,
                },
            )
        ),
    )
    print(json.dumps(_json_safe(summary), indent=2, sort_keys=True), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("first", "screen"), default="first")
    parser.add_argument(
        "--config",
        type=Path,
        default=HERE / "config_aggtrade_acceptance_nautilus.json",
    )
    parser.add_argument("--pattern-config", type=Path, default=HERE / "config_range_fvg.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--ablation",
        choices=("none", "remove_retest_contraction"),
        default="none",
        help="Diagnostic-only single-variable ablation. Ablated results are never promotable.",
    )
    parser.add_argument(
        "--reuse-first-dir",
        type=Path,
        default=None,
        help="Reuse an already-passed fixed screen-01 evidence root when running the screen suite.",
    )
    parser.add_argument(
        "--data-cache",
        type=Path,
        default=Path.home() / ".cache" / "smc4" / "candidate-08-aggtrade-acceptance",
    )
    args = parser.parse_args()
    run_suite(
        config_path=args.config.resolve(),
        pattern_config_path=args.pattern_config.resolve(),
        suite=args.suite,
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
        reuse_first_dir=(
            args.reuse_first_dir.resolve() if args.reuse_first_dir is not None else None
        ),
        ablation=args.ablation,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
