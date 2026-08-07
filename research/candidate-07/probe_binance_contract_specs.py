#!/usr/bin/env python3
"""Fetch the current official Binance USD-M contract filters for project symbols."""
from __future__ import annotations

import json
import urllib.request


SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
ENDPOINTS = (
    "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "https://fapi1.binance.com/fapi/v1/exchangeInfo",
    "https://fapi2.binance.com/fapi/v1/exchangeInfo",
    "https://fapi3.binance.com/fapi/v1/exchangeInfo",
)


def fetch() -> dict:
    errors: list[str] = []
    for endpoint in ENDPOINTS:
        try:
            request = urllib.request.Request(
                endpoint,
                headers={"User-Agent": "SMC-ICT-4-research/1.0"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                payload = json.load(response)
            result = {}
            for item in payload.get("symbols", ()):
                symbol = str(item.get("symbol"))
                if symbol not in SYMBOLS:
                    continue
                filters = {
                    str(entry["filterType"]): entry
                    for entry in item.get("filters", ())
                }
                result[symbol] = {
                    "symbol": symbol,
                    "pair": item.get("pair"),
                    "contractType": item.get("contractType"),
                    "status": item.get("status"),
                    "baseAsset": item.get("baseAsset"),
                    "quoteAsset": item.get("quoteAsset"),
                    "marginAsset": item.get("marginAsset"),
                    "pricePrecision": item.get("pricePrecision"),
                    "quantityPrecision": item.get("quantityPrecision"),
                    "tickSize": filters.get("PRICE_FILTER", {}).get("tickSize"),
                    "minPrice": filters.get("PRICE_FILTER", {}).get("minPrice"),
                    "maxPrice": filters.get("PRICE_FILTER", {}).get("maxPrice"),
                    "stepSize": filters.get("LOT_SIZE", {}).get("stepSize"),
                    "minQty": filters.get("LOT_SIZE", {}).get("minQty"),
                    "maxQty": filters.get("LOT_SIZE", {}).get("maxQty"),
                    "minNotional": filters.get("MIN_NOTIONAL", {}).get("notional"),
                }
            if set(result) != SYMBOLS:
                raise RuntimeError(f"missing symbols: {sorted(SYMBOLS.difference(result))}")
            return {"endpoint": endpoint, "symbols": result}
        except Exception as exc:  # pragma: no cover - network fallback
            errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
    raise RuntimeError("all official endpoints failed: " + " | ".join(errors))


def main() -> int:
    print(json.dumps(fetch(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
