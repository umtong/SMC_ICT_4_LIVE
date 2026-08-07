#!/usr/bin/env python3
"""Derive observed Binance USD-M price/quantity grids from verified aggTrades.

This is an implementation-contract probe, not a trading backtest. It streams the
raw checksum-verified archives and computes the exact decimal greatest common
divisor of every positive price and quantity over the frozen interval. A grid is
accepted for replay only when every observed trade lies on it. No strategy rule,
trade outcome, order or NAV is created here.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import reduce
import io
import json
from math import gcd
from pathlib import Path
from typing import Iterable
import zipfile

from data import _days, _ensure_checked_archive
from data_aggtrades_1s import (
    AGG_TRADES_DAILY_URL,
    _column_indices,
)
from smc_ict_4.manifest import write_json_atomic


@dataclass(slots=True)
class DecimalGridAccumulator:
    """Streaming exact decimal GCD with dynamically increasing scale."""

    scale: int = 0
    common_integer: int = 0
    rows: int = 0
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    maximum_decimal_places: int = 0

    def add(self, raw: str) -> None:
        value = Decimal(raw)
        if not value.is_finite() or value <= 0:
            raise ValueError(f"grid value must be finite and positive: {raw!r}")
        exponent = value.as_tuple().exponent
        places = max(0, -int(exponent))
        if places > self.scale:
            factor = 10 ** (places - self.scale)
            self.common_integer *= factor
            self.scale = places
        integer = int(value.scaleb(self.scale))
        if Decimal(integer).scaleb(-self.scale) != value:
            raise RuntimeError(f"decimal scaling was not exact: {raw!r}")
        self.common_integer = gcd(self.common_integer, integer)
        self.rows += 1
        self.maximum_decimal_places = max(self.maximum_decimal_places, places)
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    @property
    def quantum(self) -> Decimal:
        if self.rows <= 0 or self.common_integer <= 0:
            raise RuntimeError("cannot derive a grid from an empty accumulator")
        return Decimal(self.common_integer).scaleb(-self.scale)

    def payload(self) -> dict[str, object]:
        quantum = self.quantum
        return {
            "rows": self.rows,
            "scale": self.scale,
            "maximum_decimal_places": self.maximum_decimal_places,
            "common_integer": self.common_integer,
            "observed_quantum": format(quantum, "f"),
            "minimum": format(self.minimum, "f") if self.minimum is not None else None,
            "maximum": format(self.maximum, "f") if self.maximum is not None else None,
        }


def _archive_grid(path: Path) -> tuple[DecimalGridAccumulator, DecimalGridAccumulator, int]:
    prices = DecimalGridAccumulator()
    quantities = DecimalGridAccumulator()
    rows_seen = 0
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected one aggTrades CSV in {path}, found {names}")
        with archive.open(names[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            try:
                first = next(reader)
            except StopIteration:
                return prices, quantities, rows_seen
            indices, header = _column_indices(first)
            source: Iterable[list[str]] = reader if header else (row for row in [first, *reader])
            for row in source:
                if not row:
                    continue
                prices.add(row[indices["price"]])
                quantities.add(row[indices["quantity"]])
                rows_seen += 1
    return prices, quantities, rows_seen


def _merge_accumulators(items: Iterable[DecimalGridAccumulator]) -> DecimalGridAccumulator:
    output = DecimalGridAccumulator()
    for item in items:
        if item.rows == 0:
            continue
        if item.scale > output.scale:
            output.common_integer *= 10 ** (item.scale - output.scale)
            output.scale = item.scale
        item_integer = item.common_integer * 10 ** (output.scale - item.scale)
        output.common_integer = gcd(output.common_integer, item_integer)
        output.rows += item.rows
        output.maximum_decimal_places = max(
            output.maximum_decimal_places,
            item.maximum_decimal_places,
        )
        if item.minimum is not None:
            output.minimum = (
                item.minimum if output.minimum is None else min(output.minimum, item.minimum)
            )
        if item.maximum is not None:
            output.maximum = (
                item.maximum if output.maximum is None else max(output.maximum, item.maximum)
            )
    if output.rows == 0:
        raise RuntimeError("no aggregate trades were observed")
    return output


def derive_symbol_grid(
    *,
    symbol: str,
    start: date,
    end: date,
    cache_root: Path,
) -> dict[str, object]:
    if end <= start:
        raise ValueError("end must follow start")
    symbol = symbol.upper()
    root = cache_root.resolve() / symbol / "aggTrades"
    archives: list[Path] = []
    price_parts: list[DecimalGridAccumulator] = []
    quantity_parts: list[DecimalGridAccumulator] = []
    rows = 0
    for day in _days(start, end):
        stamp = day.isoformat()
        url = AGG_TRADES_DAILY_URL.format(symbol=symbol, day=stamp)
        destination = root / f"{symbol}-aggTrades-{stamp}.zip"
        archive = _ensure_checked_archive(url, destination)
        archives.append(archive)
        prices, quantities, daily_rows = _archive_grid(archive)
        price_parts.append(prices)
        quantity_parts.append(quantities)
        rows += daily_rows
    price_grid = _merge_accumulators(price_parts)
    quantity_grid = _merge_accumulators(quantity_parts)
    return {
        "symbol": symbol,
        "period": {
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
        },
        "archives": [str(path) for path in archives],
        "archive_count": len(archives),
        "raw_rows": rows,
        "price": price_grid.payload(),
        "quantity": quantity_grid.payload(),
        "contract_interpretation": (
            "observed GCD only; exact official filters remain distinct from empirical "
            "data representation and must not be inferred from strategy outcomes"
        ),
        "orders_or_pnl": False,
        "future_information": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = {
        "probe": "verified_aggtrades_observed_contract_grid",
        "symbols": {
            symbol.upper(): derive_symbol_grid(
                symbol=symbol,
                start=args.start,
                end=args.end,
                cache_root=args.data_root,
            )
            for symbol in args.symbols
        },
    }
    write_json_atomic(args.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
