#!/usr/bin/env python3
"""One-engine BTC/XRP portfolio for the frozen multiclock first-retest route.

The development-week portability screen found positive, non-overlapping
opportunities on BTCUSDT and XRPUSDT while ETHUSDT and SOLUSDT were negative.
This runner does not combine standalone equity curves. It discovers both symbol
streams causally, submits them to one NautilusTrader BacktestEngine, shares one
USDT margin account, sizes every order from current total NAV, and enforces one
portfolio-global pending/open slot.
"""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

import backtest as base
from binance_usdm_instruments import binance_usdm_perpetual
from data_aggtrades_seeded import load_aggtrade_1s_bundle_seeded
from event_signal_data import CausalTradeSignal, EVENT_SIGNAL_CLIENT_ID
from multiclock_ensemble_scenario import (
    build_ensemble_signals,
    discover_ensemble,
)
from strategy_event_signal import Candidate07EventSignalStrategyConfig
from strategy_global_slot import (
    Candidate07PortfolioSlotStrategy,
    PortfolioStrategyEvidence,
    portfolio_global_slot,
    reset_portfolio_global_slot,
)

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, MakerTakerFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import Bar, BarType, CustomData, DataType
try:
    from nautilus_trader.model.data import FundingRateUpdate
except ImportError:  # pragma: no cover
    from nautilus_trader.model.data.funding import FundingRateUpdate
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic


FROZEN_SYMBOLS = ("BTCUSDT", "XRPUSDT")


def _prefix_signal(
    signal: CausalTradeSignal,
    *,
    symbol: str,
) -> CausalTradeSignal:
    """Make scenario identity unique across one multi-instrument event log."""
    details = json.loads(signal.details_json)
    details.update(
        {
            "portfolio_symbol": symbol,
            "original_scenario_id": signal.scenario_id,
            "portfolio_selection": (
                "frozen after the development-week cross-symbol portability screen"
            ),
        }
    )
    return CausalTradeSignal(
        instrument_id=signal.instrument_id,
        scenario_id=f"{symbol}:{signal.scenario_id}",
        direction=signal.direction,
        entry_reference=signal.entry_reference,
        stop_price=signal.stop_price,
        target_price=signal.target_price,
        expected_rr=signal.expected_rr,
        source_pool_id=f"{symbol}:{signal.source_pool_id}",
        signal_kind=signal.signal_kind,
        details_json=json.dumps(details, sort_keys=True),
        observed_time_ns=signal.observed_time_ns,
        ts_event=signal.ts_event,
        ts_init=signal.ts_init,
    )


def _symbol_metrics(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    instruments = sorted({str(item["instrument_id"]) for item in trades})
    for instrument_id in instruments:
        subset = [item for item in trades if item["instrument_id"] == instrument_id]
        wins = [item for item in subset if float(item["net_pnl"]) > 0.0]
        losses = [item for item in subset if float(item["net_pnl"]) <= 0.0]
        gross_profit = sum(float(item["net_pnl"]) for item in wins)
        gross_loss = abs(sum(float(item["net_pnl"]) for item in losses))
        output[instrument_id] = {
            "trades": len(subset),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(subset) if subset else 0.0,
            "net_pnl": sum(float(item["net_pnl"]) for item in subset),
            "profit_factor": (
                gross_profit / gross_loss
                if gross_loss > 0.0
                else (float("inf") if gross_profit > 0.0 else 0.0)
            ),
        }
    return output


def run(args: argparse.Namespace) -> int:
    if args.end <= args.start:
        raise ValueError("end must follow start")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config["max_hold_minutes"] = 30
    frozen_config_path = output / "frozen_config.json"
    write_json_atomic(frozen_config_path, config)

    symbol_payloads: dict[str, dict[str, Any]] = {}
    all_signal_events: list[CustomData] = []
    all_funding_updates: list[FundingRateUpdate] = []
    data_manifest_paths: list[Path] = []
    signal_type = DataType(CausalTradeSignal)

    for symbol in FROZEN_SYMBOLS:
        symbol_root = output / symbol
        symbol_root.mkdir(parents=True, exist_ok=True)
        symbol_config = dict(config)
        symbol_config["symbol"] = symbol
        manifest_path = symbol_root / "data_manifest.json"
        data_manifest_paths.append(manifest_path)
        bundle = load_aggtrade_1s_bundle_seeded(
            symbol=symbol,
            trade_start=args.start,
            trade_end=args.end,
            positioning_warmup_days=int(config["warmup_days"]),
            event_warmup_days=args.event_warmup_days,
            cache_root=args.data_root.resolve(),
            manifest_destination=manifest_path,
        )
        bars_frame, upstream, selected, contract = discover_ensemble(
            config=symbol_config,
            bundle=bundle,
            start=args.start,
            end=args.end,
            require_retest=True,
        )
        del bars_frame
        write_json_atomic(symbol_root / "structural_upstream.json", upstream)
        write_json_atomic(symbol_root / "structural_selected.json", selected)

        instrument = binance_usdm_perpetual(symbol)
        bar_type = BarType.from_str(
            f"{instrument.id}-1-SECOND-LAST-EXTERNAL"
        )
        bars = [
            Bar(
                bar_type=bar_type,
                open=instrument.make_price(row.open),
                high=instrument.make_price(row.high),
                low=instrument.make_price(row.low),
                close=instrument.make_price(row.close),
                volume=instrument.make_qty(row.volume),
                ts_event=int(row.timestamp_ns),
                ts_init=int(row.timestamp_ns),
            )
            for row in bundle.seconds.itertuples(index=False)
        ]
        raw_signals = build_ensemble_signals(
            report=selected,
            upstream_report=upstream,
            instrument_id=instrument.id,
        )
        signals = [
            _prefix_signal(signal, symbol=symbol)
            for signal in raw_signals
        ]
        all_signal_events.extend(
            CustomData(signal_type, signal) for signal in signals
        )
        funding_updates = [
            FundingRateUpdate(
                instrument_id=instrument.id,
                rate=point.rate,
                interval=point.interval_minutes,
                next_funding_ns=None,
                ts_event=point.ts_event_ns,
                ts_init=point.ts_event_ns,
            )
            for point in bundle.minute_positioning.funding
        ]
        all_funding_updates.extend(funding_updates)
        pd.DataFrame(
            [
                {
                    "scenario_id": signal.scenario_id,
                    "instrument_id": str(signal.instrument_id),
                    "direction": signal.direction,
                    "entry_reference": signal.entry_reference,
                    "stop_price": signal.stop_price,
                    "target_price": signal.target_price,
                    "expected_rr": signal.expected_rr,
                    "source_pool_id": signal.source_pool_id,
                    "observed_time_ns": signal.observed_time_ns,
                    "delivery_time_ns": signal.ts_event,
                }
                for signal in signals
            ]
        ).to_csv(symbol_root / "signals.csv", index=False)
        symbol_payloads[symbol] = {
            "instrument": instrument,
            "bar_type": bar_type,
            "bars": bars,
            "signals": signals,
            "funding_updates": funding_updates,
            "contract": contract,
            "selected": selected,
            "loader_diagnostics": dict(bundle.diagnostics),
        }

    all_scenario_ids = [
        signal.scenario_id
        for payload in symbol_payloads.values()
        for signal in payload["signals"]
    ]
    if len(all_scenario_ids) != len(set(all_scenario_ids)):
        raise RuntimeError("portfolio scenario identifiers are not unique")

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        )
    )
    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    fill_model = FillModel(
        prob_fill_on_limit=float(config["fill_model"]["prob_fill_on_limit"]),
        prob_slippage=float(config["fill_model"]["prob_slippage"]),
        random_seed=int(config["fill_model"]["random_seed"]),
    )
    reset_portfolio_global_slot()
    strategies: list[Candidate07PortfolioSlotStrategy] = []

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[
                Money(Decimal(str(config["initial_nav"])), usdt)
            ],
            base_currency=usdt,
            default_leverage=Decimal(
                str(config["venue"]["default_leverage"])
            ),
            fill_model=fill_model,
            fee_model=MakerTakerFeeModel(),
            bar_adaptive_high_low_ordering=bool(
                config["venue"]["bar_adaptive_high_low_ordering"]
            ),
            use_position_ids=True,
            use_reduce_only=True,
            reject_stop_orders=False,
        )
        for symbol in FROZEN_SYMBOLS:
            payload = symbol_payloads[symbol]
            engine.add_instrument(payload["instrument"])
            engine.add_data(payload["bars"])

        all_signal_events.sort(
            key=lambda item: (
                int(item.ts_event),
                str(item.data.instrument_id),
                str(item.data.scenario_id),
            )
        )
        engine.add_data(all_signal_events, client_id=EVENT_SIGNAL_CLIENT_ID)
        if all_funding_updates:
            all_funding_updates.sort(
                key=lambda item: (int(item.ts_event), str(item.instrument_id))
            )
            engine.add_data(all_funding_updates)

        for symbol in FROZEN_SYMBOLS:
            payload = symbol_payloads[symbol]
            strategy = Candidate07PortfolioSlotStrategy(
                Candidate07EventSignalStrategyConfig(
                    instrument_id=payload["instrument"].id,
                    bar_type=payload["bar_type"],
                    trade_start_ns=base._utc_ns(args.start),
                    trade_end_ns=base._utc_ns(args.end),
                    initial_nav=Decimal(str(config["initial_nav"])),
                    risk_fraction=Decimal(str(config["risk_fraction"])),
                    risk_funding_reserve_bps=Decimal(
                        str(config["risk_funding_reserve_bps"])
                    ),
                    maximum_hold_seconds=int(config["max_hold_minutes"]) * 60,
                    minimum_rr=Decimal("0"),
                )
            )
            strategies.append(strategy)
            engine.add_strategy(strategy)

        engine.run()
        evidence = PortfolioStrategyEvidence(strategies)
        slot = portfolio_global_slot()
        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)
        fills.to_csv(output / "fills.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)
        pd.DataFrame(evidence.nav_series).to_csv(output / "nav.csv", index=False)
        pd.DataFrame(evidence.trade_diagnostics).to_csv(
            output / "trades.csv",
            index=False,
        )
        write_events(output / "events.jsonl", evidence.research_events)
        write_json_atomic(
            output / "scenario_diagnostics.json",
            {
                "observations": list(evidence.scenario_diagnostics),
                "global_slot_diagnostics": slot.diagnostics,
                "reservation_history": slot.reservation_history,
            },
        )

        metrics = base._metrics(
            config=config,
            stage=args.stage,
            start=args.start,
            end=args.end,
            strategy=evidence,
            fills=fills,
            positions=positions,
            account=account,
            funding_points=len(all_funding_updates),
        )
        trades = [dict(item) for item in evidence.trade_diagnostics]
        metrics["by_instrument"] = _symbol_metrics(trades)
        metrics["structural_contract"] = {
            "family": "15S_sweep_multiclock_first_retest_portfolio",
            "symbols": list(FROZEN_SYMBOLS),
            "logic_or_parameter_change_from_standalone": False,
            "per_symbol": {
                symbol: {
                    "selected_summary": symbol_payloads[symbol]["selected"][
                        "summary"
                    ],
                    "execution_contract": symbol_payloads[symbol]["contract"],
                    "loader_diagnostics": symbol_payloads[symbol][
                        "loader_diagnostics"
                    ],
                }
                for symbol in FROZEN_SYMBOLS
            },
            "future_information": False,
            "orders_or_pnl_created_by_preprocessor": False,
        }
        metrics["signal_contract"] = {
            "per_symbol": {
                symbol: len(symbol_payloads[symbol]["signals"])
                for symbol in FROZEN_SYMBOLS
            },
            "total_causal_signals": len(all_signal_events),
            "global_scenario_ids_unique": True,
            "orders_or_pnl_created_by_preprocessor": False,
            "engine": "one NautilusTrader BacktestEngine",
        }
        metrics["execution_contract"].update(
            {
                "portfolio": "BTCUSDT and XRPUSDT in one USDT margin account",
                "instrument_selection": (
                    "frozen after unchanged W1 portability screen; no symbol score"
                ),
                "signal_arbitration": (
                    "first causally delivered eligible signal reserves the one "
                    "portfolio-global slot"
                ),
                "global_pending_or_open_slots": 1,
                "current_full_nav_sizing_across_symbols": True,
                "take_profit_order_type": "MARKET_IF_TOUCHED",
                "target_cost_viability_required": True,
                "arbitrary_notional_cap": False,
                "model_score_risk_multiplier": False,
            }
        )
        metrics["portfolio_slot_contract"] = {
            "slot_free_after_run": slot.is_free,
            "owner_after_run": slot.owner_instrument_id,
            "reservations": sum(
                item["action"] == "RESERVE"
                for item in slot.reservation_history
            ),
            "releases": sum(
                item["action"] == "RELEASE"
                for item in slot.reservation_history
            ),
            "busy_signal_rejections": sum(
                item.get("reason")
                == "PORTFOLIO_GLOBAL_SLOT_OR_WINDOW_INELIGIBLE"
                and item.get("global_owner_instrument_id") is not None
                for item in slot.diagnostics
            ),
            "single_slot_invariant": bool(
                metrics["weekly_gate"]["checks"]["single_slot"]
            ),
        }
        metrics["eligible_for_frozen_week_2"] = bool(
            metrics["weekly_gate"]["passed"]
        )
        write_json_atomic(output / "metrics.json", base._json_safe(metrics))
        write_json_atomic(
            output / "summary.json",
            {
                "candidate": "candidate-07",
                "family": "BTC_XRP_multiclock_first_retest_portfolio",
                "stage": args.stage,
                "period": {
                    "start": args.start.isoformat(),
                    "end_exclusive": args.end.isoformat(),
                },
                "source_commit_expected": args.source_commit,
                "engine": "one NautilusTrader BacktestEngine",
                "symbols": list(FROZEN_SYMBOLS),
                "daily_geometric_growth": metrics[
                    "daily_geometric_growth"
                ],
                "net_return": metrics["net_return"],
                "trades": metrics["trades"],
                "wins": metrics["wins"],
                "losses": metrics["losses"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["max_drawdown"],
                "active_days": metrics["active_days"],
                "weekly_gate": metrics["weekly_gate"],
                "by_instrument": metrics["by_instrument"],
                "portfolio_slot_contract": metrics[
                    "portfolio_slot_contract"
                ],
            },
        )
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=(
                    f"candidate-07-{args.stage}-btc-xrp-"
                    f"{args.start.isoformat()}"
                ),
                candidate="candidate-07",
                config_path=frozen_config_path,
                data_manifest_path=data_manifest_paths[0],
                extra={
                    "stage": args.stage,
                    "start": args.start.isoformat(),
                    "end_exclusive": args.end.isoformat(),
                    "engine": "one NautilusTrader BacktestEngine",
                    "symbols": list(FROZEN_SYMBOLS),
                    "data_manifests": [
                        str(path) for path in data_manifest_paths
                    ],
                    "global_pending_or_open_slots": 1,
                },
            ),
        )
        print(json.dumps(base._json_safe(metrics), indent=2, sort_keys=True))
        return 0
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stage", default="week-1")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    parser.add_argument("--event-warmup-days", type=int, default=1)
    parser.add_argument("--source-commit", default="unknown")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
