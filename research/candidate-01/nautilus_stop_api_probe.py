#!/usr/bin/env python3
"""Discover the actual NautilusTrader 1.230.0 stop-entry factory contract."""
from __future__ import annotations

import importlib
import inspect
import pkgutil

import nautilus_trader
from nautilus_trader.model.enums import OrderType, TriggerType


def main() -> int:
    print("ORDER_TYPES", OrderType.STOP_MARKET, OrderType.STOP_LIMIT)
    print("TRIGGER", TriggerType.LAST_PRICE)
    found = []
    for item in pkgutil.walk_packages(
        nautilus_trader.__path__,
        prefix="nautilus_trader.",
    ):
        name = item.name
        if "order" not in name.lower() and "factor" not in name.lower():
            continue
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        factory = getattr(module, "OrderFactory", None)
        if factory is None:
            continue
        found.append(name)
        print("ORDER_FACTORY_MODULE", name)
        print("BRACKET_SIGNATURE", inspect.signature(factory.bracket))
        print("STOP_MARKET_SIGNATURE", inspect.signature(factory.stop_market))
        print("BRACKET_DOC", factory.bracket.__doc__)
        break
    if not found:
        raise RuntimeError("OrderFactory was not found in installed Nautilus package")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
