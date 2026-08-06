#!/usr/bin/env python3
"""Verify ParquetDataCatalog -> BacktestNode -> native execution/accounting."""
from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def instrument() -> CryptoPerpetual:
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        multiplier=Quantity.from_int(1),
        lot_size=Quantity.from_str("0.001"),
        min_quantity=Quantity.from_str("0.001"),
        min_notional=Money.from_str("5 USDT"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
    )


def main() -> int:
    output = Path("artifacts/candidate-03/nt-smoke").resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog_path = output / "catalog"
    shutil.rmtree(catalog_path, ignore_errors=True)
    catalog_path.mkdir(parents=True)

    inst = instrument()
    quotes: list[QuoteTick] = []
    base = 1_700_000_000_000_000_000
    for index, mid in enumerate((40_000.0, 40_010.0, 40_020.0, 40_030.0, 40_040.0, 40_050.0)):
        ts = base + index * 1_000_000_000
        quotes.append(
            QuoteTick(
                instrument_id=inst.id,
                bid_price=inst.make_price(mid - 0.5),
                ask_price=inst.make_price(mid + 0.5),
                bid_size=inst.make_qty(2),
                ask_size=inst.make_qty(2),
                ts_event=ts,
                ts_init=ts,
            )
        )

    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([inst])
    catalog.write_data(quotes)

    strategy = ImportableStrategyConfig(
        strategy_path="scripts.nt_candidate_smoke_strategy:CandidateNTSmoke",
        config_path="scripts.nt_candidate_smoke_strategy:CandidateNTSmokeConfig",
        config={
            "instrument_id": inst.id,
            "trade_size": Decimal("0.01"),
        },
    )
    run_config = BacktestRunConfig(
        engine=BacktestEngineConfig(strategies=[strategy]),
        data=[
            BacktestDataConfig(
                catalog_path=str(catalog_path),
                data_cls=QuoteTick,
                instrument_id=inst.id,
            )
        ],
        venues=[
            BacktestVenueConfig(
                name="BINANCE",
                oms_type="NETTING",
                account_type="MARGIN",
                base_currency="USDT",
                starting_balances=["100_000 USDT"],
                default_leverage=100.0,
                book_type="L1_MBP",
                reject_stop_orders=False,
                use_position_ids=False,
                use_reduce_only=True,
                bar_execution=False,
                trade_execution=False,
                liquidity_consumption=True,
            )
        ],
        raise_exception=True,
        dispose_on_completion=False,
    )
    node = BacktestNode(configs=[run_config])
    results = node.run()
    if len(results) != 1:
        raise RuntimeError(f"expected one result, got {len(results)}")
    result = results[0]
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError("BacktestNode did not retain its engine")
    account = engine.kernel.portfolio.account(inst.venue)
    payload = {
        "engine": "NautilusTrader BacktestNode",
        "total_events": result.total_events,
        "total_orders": result.total_orders,
        "total_positions": result.total_positions,
        "summary": dict(result.summary),
        "stats_pnls": result.stats_pnls,
        "stats_returns": result.stats_returns,
        "account": str(account),
    }
    (output / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    if result.total_orders < 2 or result.total_positions < 1:
        raise RuntimeError("native order/position path did not complete")
    node.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
