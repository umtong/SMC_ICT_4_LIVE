"""NautilusTrader-only replay for one-minute index valuation execution."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd

import backtest as base
from data import write_bundle_summary
from data_index_reference import load_index_positioning_bundle
from flow_data import AggressorFlow, FLOW_CLIENT_ID
from index_reference import INDEX_REFERENCE_CLIENT_ID, IndexPriceReference
from positioning_data import POSITIONING_CLIENT_ID, PositioningSnapshot
from strategy_valuation_mtf import (
    Candidate07MTFValuationStrategy,
    Candidate07MTFValuationStrategyConfig,
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
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic


NS_PER_MINUTE = 60_000_000_000


def _optional_float(value) -> float | None:
    return None if pd.isna(value) else float(value)


def run_week(
    *,
    config_path: Path,
    stage: str,
    start: date,
    end: date,
    output: Path,
    cache_root: Path,
) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    data_manifest_path = output / "data_manifest.json"
    bundle = load_index_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=start,
        trade_end=end,
        warmup_days=int(config["warmup_days"]),
        cache_root=cache_root,
        manifest_destination=data_manifest_path,
    )
    write_bundle_summary(output / "data_summary.json", bundle)

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")

    positioning_type = DataType(PositioningSnapshot)
    positioning_events: list[CustomData] = []
    for row in bundle.metrics.itertuples():
        timestamp_ns = int(row.timestamp_ns)
        payload = PositioningSnapshot(
            instrument_id=instrument.id,
            open_interest=float(row.sum_open_interest),
            open_interest_value=float(row.sum_open_interest_value),
            top_trader_account_ratio=_optional_float(
                row.count_toptrader_long_short_ratio
            ),
            top_trader_position_ratio=_optional_float(
                row.sum_toptrader_long_short_ratio
            ),
            global_long_short_ratio=_optional_float(row.count_long_short_ratio),
            taker_long_short_ratio=_optional_float(row.sum_taker_long_short_vol_ratio),
            ts_event=timestamp_ns,
            ts_init=timestamp_ns,
        )
        positioning_events.append(CustomData(positioning_type, payload))

    index_type = DataType(IndexPriceReference)
    index_events: list[CustomData] = []
    flow_type = DataType(AggressorFlow)
    flow_events: list[CustomData] = []
    bars: list[Bar] = []
    for trade_row, index_row in zip(
        bundle.frame.itertuples(),
        bundle.index_frame.itertuples(),
        strict=True,
    ):
        raw_close_ts = int(trade_row.Index.value)
        if raw_close_ts != int(index_row.Index.value):
            raise RuntimeError("trade and index rows lost exact timestamp alignment")
        minute_boundary = ((raw_close_ts // NS_PER_MINUTE) + 1) * NS_PER_MINUTE
        flow_payload = AggressorFlow(
            instrument_id=instrument.id,
            total_volume=float(trade_row.volume),
            taker_buy_volume=float(trade_row.taker_buy_base),
            ts_event=minute_boundary,
            ts_init=minute_boundary,
        )
        flow_events.append(CustomData(flow_type, flow_payload))
        index_payload = IndexPriceReference(
            instrument_id=instrument.id,
            open=float(index_row.open),
            high=float(index_row.high),
            low=float(index_row.low),
            close=float(index_row.close),
            ts_event=minute_boundary,
            ts_init=minute_boundary,
        )
        index_events.append(CustomData(index_type, index_payload))
        bar_ts = minute_boundary + 1
        bars.append(
            Bar(
                bar_type=bar_type,
                open=instrument.make_price(trade_row.open),
                high=instrument.make_price(trade_row.high),
                low=instrument.make_price(trade_row.low),
                close=instrument.make_price(trade_row.close),
                volume=instrument.make_qty(trade_row.volume),
                ts_event=bar_ts,
                ts_init=bar_ts,
            )
        )

    funding_updates: list[FundingRateUpdate] = []
    for point in bundle.funding:
        funding_updates.append(
            FundingRateUpdate(
                instrument_id=instrument.id,
                rate=point.rate,
                interval=point.interval_minutes,
                next_funding_ns=None,
                ts_event=point.ts_event_ns,
                ts_init=point.ts_event_ns,
            )
        )

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
    strategy = Candidate07MTFValuationStrategy(
        Candidate07MTFValuationStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trade_start_ns=base._utc_ns(start),
            trade_end_ns=base._utc_ns(end),
            initial_nav=Decimal(str(config["initial_nav"])),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            risk_funding_reserve_bps=Decimal(
                str(config["risk_funding_reserve_bps"])
            ),
            max_hold_minutes=int(config["max_hold_minutes"]),
            logic_json=json.dumps(config["logic"], sort_keys=True),
            positioning_logic_json=json.dumps(
                config["positioning_logic"],
                sort_keys=True,
            ),
        )
    )

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(Decimal(str(config["initial_nav"])), usdt)],
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
        # Every completed data item is delivered at the exact next-minute
        # boundary. The matching trade bar follows one nanosecond later.
        engine.add_data(positioning_events, client_id=POSITIONING_CLIENT_ID)
        engine.add_data(index_events, client_id=INDEX_REFERENCE_CLIENT_ID)
        engine.add_data(flow_events, client_id=FLOW_CLIENT_ID)
        engine.add_data(bars)
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
        pd.DataFrame(strategy.trade_diagnostics).to_csv(output / "trades.csv", index=False)
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
                "signal_state": (
                    "completed five-minute trade/index basis, aggressor flow and "
                    "USD-M positioning; subsequent completed one-minute index basis"
                ),
                "signal_clock": "FIVE_MINUTE",
                "execution_clock": "ONE_MINUTE",
                "direction_source": "sign of trade close minus Binance index close",
                "parent_entry_order": "MARKET on the next completed one-minute bar",
                "scenario_target_fixed_at_entry_ready": True,
                "bar_ordering": (
                    "flow and index reference at next-minute boundary, trade bar one "
                    "nanosecond later; positioning at completed five-minute boundary"
                ),
            }
        )
        metrics["mtf_valuation_contract"] = {
            "index_events": len(index_events),
            "positioning_events": len(positioning_events),
            "flow_events": len(flow_events),
            "dislocation_episodes": strategy.router.consumed_pool_count,
            "use_open_interest": strategy.logic.use_open_interest,
            "five_minute_stop_used": False,
            "target_recomputed_after_signal": False,
            "index_interpolation_used": False,
            "positioning_interpolation_used": False,
        }
        write_json_atomic(output / "metrics.json", base._json_safe(metrics))
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=f"candidate-07-mtf-valuation-{stage}-{start.isoformat()}",
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
                    "aggressor_flow_events": len(flow_events),
                    "index_reference_events": len(index_events),
                    "positioning_events": len(positioning_events),
                    "funding_updates": len(funding_updates),
                    "signal_model": (
                        "five-minute OI/index dislocation with one-minute contraction entry"
                    ),
                    "candidate_version": "candidate-07-mtf-index-valuation-v1",
                },
            ),
        )
        return metrics
    finally:
        engine.dispose()


__all__ = ["run_week"]
