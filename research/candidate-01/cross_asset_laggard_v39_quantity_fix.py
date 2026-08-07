#!/usr/bin/env python3
"""Run candidate v39 with period-correct SOL/XRP quantity filters.

The first v39 execution proved the strategy path but exposed an adapter metadata
error: historical SOL aggregate trades include sub-one quantities, so declaring
one whole SOL as the size increment caused NautilusTrader to reject an official
0.4 trade before the control engine could start. This wrapper changes only the
instrument quantity metadata. Candidate logic, frozen week, costs, risk, target,
stop and order lifecycle remain unchanged.
"""
from __future__ import annotations

import cross_asset_laggard_v39 as base
from nautilus_multi_tick_plan_backtest import InstrumentSpec


def install_period_quantity_specs() -> None:
    base.INSTRUMENT_SPECS["SOLUSDT"] = InstrumentSpec(
        symbol="SOLUSDT",
        base_currency="SOL",
        price_increment="0.001",
        quantity_increment="0.01",
        min_quantity="0.01",
        min_notional=5.0,
        min_price="0.001",
        max_price="100000.0",
    )
    base.INSTRUMENT_SPECS["XRPUSDT"] = InstrumentSpec(
        symbol="XRPUSDT",
        base_currency="XRP",
        price_increment="0.0001",
        quantity_increment="0.1",
        min_quantity="0.1",
        min_notional=5.0,
        min_price="0.0001",
        max_price="10000.0",
    )


if __name__ == "__main__":
    install_period_quantity_specs()
    raise SystemExit(base.run(base.build_parser().parse_args()))
