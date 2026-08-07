#!/usr/bin/env python3
"""NautilusTrader replay for the pre-attack auction-value candidate.

Signal discovery is causal preprocessing only.  Orders, fills, fees, funding,
positions, PnL, cash and NAV are produced exclusively by NautilusTrader's
``BacktestEngine``.  The selected scenario is:

    five-minute liquidity contact
    -> volume-time inefficient attack
    -> pool reclaim with no more opposite quote than attack quote
    -> target the last complete pre-contact 15-second VWAP.

The structural target is part of the scenario and is not subjected to an
arbitrary minimum-R multiple.  At signal delivery the execution strategy still
rejects wrong-side or already-delivered geometry, sizes from current full NAV at
3% planned loss including fees, adverse ticks and funding reserve, and maintains
one pending/open slot.
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
import diagnose_aggtrade_volume_time as volume_time
import diagnose_impact_resilience_1s as impact
import diagnose_pre_attack_value as pre_attack_value
from data_aggtrades_1s import load_aggtrade_1s_bundle
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from diagnose_impact_resilience_1s_v2 import attach_causal_context_gap_safe
from diagnose_session_handoff import _align_positioning
from event_signal_data import CausalTradeSignal, EVENT_SIGNAL_CLIENT_ID
from strategy_event_signal import (
    Candidate07EventSignalStrategy,
    Candidate07EventSignalStrategyConfig,
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
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic


NS_PER_SECOND = 1_000_000_000


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except Exception:
            pass
    return str(value)


def build_causal_signals(
    *,
    report: Mapping[str, Any],
    upstream_report: Mapping[str, Any],
    instrument_id: InstrumentId,
) -> list[CausalTradeSignal]:
    """Convert structural entries to immutable post-observation data events."""
    upstream = {
        str(item.get("scenario_id")): item
        for item in upstream_report.get("scenarios", ())
    }
    output: list[CausalTradeSignal] = []
    for item in report.get("scenarios", ()):
        if item.get("outcome") != "ENTRY_READY":
            continue
        observed_time_ns = int(item["entry_observed"]["timestamp_ns"])
        delivery_ns = observed_time_ns + 1
        source_id = str(item.get("source_scenario_id", ""))
        source = upstream.get(source_id, {})
        details = {
            "structural_family": "pre_attack_auction_value",
            "target_statistic": "prior_complete_15s_vwap",
            "source_scenario_id": source_id,
            "source_pool_id": source.get("pool_id"),
            "source_pool_side": source.get("pool_side"),
            "liquidity_level": source.get("liquidity_level"),
            "inventory_state": item.get("inventory_state"),
            "upstream_recovery_quote_ratio": item.get(
                "upstream_recovery_quote_ratio"
            ),
            "upstream_impact_asymmetry": item.get(
                "upstream_impact_asymmetry"
            ),
            "target_details": item.get("target_details"),
            "structural_target_rr": item.get("target_rr"),
        }
        output.append(
            CausalTradeSignal(
                instrument_id=instrument_id,
                scenario_id=str(item["scenario_id"]),
                direction=str(item["direction"]),
                entry_reference=float(item["entry"]),
                stop_price=float(item["stop"]),
                target_price=float(item["target"]),
                expected_rr=float(item["target_rr"]),
                source_pool_id=str(source.get("pool_id") or source_id),
                signal_kind="PRE_ATTACK_VALUE_VWAP",
                details_json=json.dumps(_jsonable(details), sort_keys=True),
                observed_time_ns=observed_time_ns,
                ts_event=delivery_ns,
                ts_init=delivery_ns,
            )
        )
    output.sort(key=lambda item: (item.ts_event, item.scenario_id))
    if len({item.scenario_id for item in output}) != len(output):
        raise RuntimeError("duplicate causal scenario identifiers")
    return output


def discover_structural_signals(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Reproduce the frozen structural route from the verified data bundle."""
    impact_logic = impact.ImpactLogic()
    impact_logic.validate()
    volume_logic = volume_time.VolumeTimeLogic()
    volume_logic.validate()
    target_logic = pre_attack_value.PreAttackValueLogic(target_statistic="vwap")
    target_logic.validate()

    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=impact_logic.minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five = _align_positioning(
        five,
        bundle.minute_positioning.metrics,
        oi_period=impact_logic.oi_period,
        oi_impulse_rank=impact_logic.oi_impulse_rank,
    )
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    event_seconds = bundle.seconds.copy()
    event_seconds["close_time_ns"] = event_seconds["timestamp_ns"].astype("int64")
    bars = attach_causal_context_gap_safe(
        event_seconds,
        minute,
        five,
        history_windows=impact_logic.history_windows,
        flow_quantile=impact_logic.flow_quantile,
    )
    event_start_ns = int(bars.iloc[0]["timestamp_ns"])
    one_all = impact._pool_confirmations(
        minute,
        timeframe="1M",
        radius=impact_logic.one_minute_pivot_radius,
    )
    five_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=impact_logic.five_minute_pivot_radius,
    )
    one_pools, one_pre = preconsume_before_event_window(
        one_all,
        minute,
        event_start_ns=event_start_ns,
    )
    five_pools, five_pre = preconsume_before_event_window(
        five_all,
        minute,
        event_start_ns=event_start_ns,
    )
    targets = {"1M": one_pools, "5M": five_pools}
    upstream = volume_time.diagnose(
        bars,
        source_pools=five_pools,
        target_pools=targets,
        trade_start_ns=base._utc_ns(start),
        trade_end_ns=base._utc_ns(end),
        max_hold_seconds=int(config["max_hold_minutes"]) * 60,
        logic=volume_logic,
        require_oi_release=False,
    )
    selected = pre_attack_value.diagnose(
        bars,
        upstream_report=upstream,
        max_hold_seconds=int(config["max_hold_minutes"]) * 60,
        logic=target_logic,
    )
    contract = {
        "one_minute_preconsumption": one_pre,
        "five_minute_preconsumption": five_pre,
        "upstream_summary": upstream["summary"],
        "selected_summary": selected["summary"],
        "upstream_oi_release_required": False,
        "target": "last complete 15-second pre-contact VWAP",
        "minimum_structural_rr_filter": False,
        "future_information": False,
    }
    return bars, upstream, selected, contract


def run_week(
    *,
    config_path: Path,
    stage: str,
    start: date,
    end: date,
    output: Path,
    cache_root: Path,
    event_warmup_days: int = 1,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    data_manifest_path = output / "data_manifest.json"
    bundle = load_aggtrade_1s_bundle(
        symbol=str(config["symbol"]),
        trade_start=start,
        trade_end=end,
        positioning_warmup_days=int(config["warmup_days"]),
        event_warmup_days=event_warmup_days,
        cache_root=cache_root,
        manifest_destination=data_manifest_path,
    )
    context_bars, upstream_report, structural_report, structural_contract = (
        discover_structural_signals(
            config=config,
            bundle=bundle,
            start=start,
            end=end,
        )
    )
    write_json_atomic(
        output / "structural_upstream.json",
        _jsonable(upstream_report),
    )
    write_json_atomic(
        output / "structural_selected.json",
        _jsonable(structural_report),
    )

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-SECOND-LAST-EXTERNAL")
    bars: list[Bar] = []
    for row in bundle.seconds.itertuples(index=False):
        timestamp_ns = int(row.timestamp_ns)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=instrument.make_price(row.open),
                high=instrument.make_price(row.high),
                low=instrument.make_price(row.low),
                close=instrument.make_price(row.close),
                volume=instrument.make_qty(row.volume),
                ts_event=timestamp_ns,
                ts_init=timestamp_ns,
            )
        )

    causal_signals = build_causal_signals(
        report=structural_report,
        upstream_report=upstream_report,
        instrument_id=instrument.id,
    )
    signal_type = DataType(CausalTradeSignal)
    signal_events = [CustomData(signal_type, item) for item in causal_signals]
    pd.DataFrame(
        [
            {
                "scenario_id": item.scenario_id,
                "direction": item.direction,
                "entry_reference": item.entry_reference,
                "stop_price": item.stop_price,
                "target_price": item.target_price,
                "expected_rr": item.expected_rr,
                "source_pool_id": item.source_pool_id,
                "observed_time_ns": item.observed_time_ns,
                "delivery_time_ns": item.ts_event,
            }
            for item in causal_signals
        ]
    ).to_csv(output / "signals.csv", index=False)

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
    strategy = Candidate07EventSignalStrategy(
        Candidate07EventSignalStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trade_start_ns=base._utc_ns(start),
            trade_end_ns=base._utc_ns(end),
            initial_nav=Decimal(str(config["initial_nav"])),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            risk_funding_reserve_bps=Decimal(
                str(config["risk_funding_reserve_bps"])
            ),
            maximum_hold_seconds=int(config["max_hold_minutes"]) * 60,
            # The causal target itself defines the reward.  Applying the old
            # remote-liquidity 1.25R veto would change the selected scenario.
            minimum_rr=Decimal("0"),
        )
    )

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[
                Money(Decimal(str(config["initial_nav"])), usdt)
            ],
            base_currency=usdt,
            default_leverage=Decimal(str(config["venue"]["default_leverage"])),
            fill_model=fill_model,
            fee_model=MakerTakerFeeModel(),
            bar_adaptive_high_low_ordering=bool(
                config["venue"]["bar_adaptive_high_low_ordering"]
            ),
            use_position_ids=True,
            use_reduce_only=True,
            reject_stop_orders=False,
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_data(signal_events, client_id=EVENT_SIGNAL_CLIENT_ID)
        if funding_updates:
            engine.add_data(funding_updates)
        engine.add_strategy(strategy)
        engine.run()

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)
        fills.to_csv(output / "fills.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)
        pd.DataFrame(strategy.nav_series).to_csv(output / "nav.csv", index=False)
        pd.DataFrame(strategy.trade_diagnostics).to_csv(
            output / "trades.csv",
            index=False,
        )
        write_events(output / "events.jsonl", strategy.research_events)
        write_json_atomic(
            output / "scenario_diagnostics.json",
            {"observations": list(strategy.scenario_diagnostics)},
        )
        metrics = base._metrics(
            config=config,
            stage=stage,
            start=start,
            end=end,
            strategy=strategy,
            fills=fills,
            positions=positions,
            account=account,
            funding_points=len(funding_updates),
        )
        metrics["execution_contract"].update(
            {
                "bar_type": str(bar_type),
                "signal_delivery": (
                    "one nanosecond after the completed recovery-terminal second"
                ),
                "market_data": (
                    "checksum-verified Binance USD-M aggTrades reduced to causal "
                    "one-second bars; no-trade seconds are zero-flow observations"
                ),
                "selected_route": "volume-time failed auction to pre-attack 15s VWAP",
                "minimum_rr_veto": False,
                "entry_delay": "next Nautilus-matchable one-second bar after signal delivery",
            }
        )
        metrics["structural_contract"] = structural_contract
        metrics["signal_contract"] = {
            "structural_entries": int(structural_report["summary"]["entry_ready"]),
            "causal_signals": len(causal_signals),
            "orders_or_pnl_created_by_preprocessor": False,
            "engine": "NautilusTrader BacktestEngine",
        }
        write_json_atomic(output / "metrics.json", base._json_safe(metrics))
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=f"candidate-07-pre-attack-value-{stage}-{start.isoformat()}",
                candidate="candidate-07",
                config_path=config_path,
                data_manifest_path=data_manifest_path,
                extra={
                    "stage": stage,
                    "start": start.isoformat(),
                    "end_exclusive": end.isoformat(),
                    "instrument_id": str(instrument.id),
                    "bar_type": str(bar_type),
                    "engine": "NautilusTrader BacktestEngine",
                    "bars": len(bars),
                    "causal_signals": len(causal_signals),
                    "funding_updates": len(funding_updates),
                    "signal_model": "pre-attack auction-value delivery",
                },
            ),
        )
        return metrics
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    metrics = run_week(
        config_path=args.config,
        stage=args.stage,
        start=args.start,
        end=args.end,
        output=args.output,
        cache_root=args.data_root,
        event_warmup_days=args.event_warmup_days,
    )
    print(
        json.dumps(
            {
                "stage": args.stage,
                "daily_geometric_growth": metrics["daily_geometric_growth"],
                "net_return": metrics["net_return"],
                "trades": metrics["trades"],
                "wins": metrics["wins"],
                "losses": metrics["losses"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["max_drawdown"],
                "active_days": metrics["active_days"],
                "single_winner_share": metrics["single_winner_share"],
                "weekly_gate": metrics["weekly_gate"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
