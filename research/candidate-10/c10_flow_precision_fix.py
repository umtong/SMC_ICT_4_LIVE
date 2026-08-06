"""Controlled aggregate-trade precision adapter for candidate 10 v3.

Binance CSV values omit trailing zeroes, while Nautilus requires every TradeTick
Price and Quantity to carry the exact instrument precision. This module changes
only object representation (for example 0.01 -> 0.010); timestamps, values,
aggressor mapping, strategy parameters and execution assumptions are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from nautilus_trader.model.objects import Price as NautilusPrice
from nautilus_trader.model.objects import Quantity as NautilusQuantity

import c10_flow_research as _research
from c10_strategy import make_cost_loaded_btc_perpetual

_INSTRUMENT = make_cost_loaded_btc_perpetual()
_ORIGINAL_LOADER = _research.load_aggtrade_ticks
_ORIGINAL_PRICE = _research.Price
_ORIGINAL_QUANTITY = _research.Quantity


class _InstrumentPriceFactory:
    @staticmethod
    def from_str(raw: str) -> NautilusPrice:
        return NautilusPrice(
            float(raw),
            precision=_INSTRUMENT.price_precision,
        )


class _InstrumentQuantityFactory:
    @staticmethod
    def from_str(raw: str) -> NautilusQuantity:
        return NautilusQuantity(
            float(raw),
            precision=_INSTRUMENT.size_precision,
        )


def load_aggtrade_ticks(
    paths: Iterable[Path],
    instrument_id: Any,
) -> tuple[list[Any], dict[str, Any]]:
    if instrument_id != _INSTRUMENT.id:
        raise RuntimeError(
            f"precision adapter instrument mismatch: {instrument_id} != {_INSTRUMENT.id}",
        )
    _research.Price = _InstrumentPriceFactory
    _research.Quantity = _InstrumentQuantityFactory
    try:
        ticks, quality = _ORIGINAL_LOADER(paths, instrument_id)
    finally:
        _research.Price = _ORIGINAL_PRICE
        _research.Quantity = _ORIGINAL_QUANTITY
    quality["precision_normalization"] = {
        "price_precision": _INSTRUMENT.price_precision,
        "size_precision": _INSTRUMENT.size_precision,
        "value_changed": False,
        "representation_only": True,
    }
    return ticks, quality


# run_flow_backtest resolves this module global at call time, so replacing only
# the loader preserves an exact controlled rerun of the same strategy.
_research.load_aggtrade_ticks = load_aggtrade_ticks

reproducible_weeks = _research.reproducible_weeks
run_flow_backtest = _research.run_flow_backtest

__all__ = ["reproducible_weeks", "run_flow_backtest"]
