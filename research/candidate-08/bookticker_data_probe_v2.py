"""Header-stable streaming revision of the Binance USD-M bookTicker data probe.

V1 already enforced official checksum verification and streamed the archive without retaining a
full day in memory.  V2 fixes one parser boundary: when an archive contains an explicit header in a
non-canonical column order, that discovered order must remain active for every subsequent pandas
chunk and each yielded frame must then be reordered to the canonical schema.

This module remains a data-contract probe only.  It creates no trading signal, order, fill, account
state, PnL simulation or backtest engine.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterator
import zipfile

import pandas as pd

import bookticker_data_probe as base
from data import BinanceDataError


PROBE_REVISION = "BINANCE_USDM_BOOKTICKER_DATA_CONTRACT_V2_HEADER_STABLE_STREAMING"


def _read_chunks(
    path: Path,
    *,
    chunksize: int = 500_000,
) -> tuple[str, Iterator[pd.DataFrame]]:
    """Stream one CSV while preserving its discovered schema across all chunks."""

    archive = zipfile.ZipFile(path)
    members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(members) != 1:
        archive.close()
        raise BinanceDataError(f"expected one CSV in {path.name}, found {members}")
    handle = archive.open(members[0])
    reader = pd.read_csv(handle, header=None, chunksize=chunksize, low_memory=False)

    def iterator() -> Iterator[pd.DataFrame]:
        first = True
        active_columns: tuple[str, ...] | None = None
        try:
            for raw in reader:
                if raw.shape[1] < len(base.BOOK_TICKER_COLUMNS):
                    raise BinanceDataError(
                        f"bookTicker file exposed {raw.shape[1]} columns; expected at least 7"
                    )
                raw = raw.iloc[:, : len(base.BOOK_TICKER_COLUMNS)].copy()
                if first:
                    first = False
                    discovered = base._normalise_header(raw.iloc[0].tolist())
                    if discovered is not None:
                        active_columns = discovered
                        raw = raw.iloc[1:].copy()
                    else:
                        active_columns = base.BOOK_TICKER_COLUMNS
                if active_columns is None:
                    raise BinanceDataError("bookTicker active schema was not initialized")
                raw.columns = active_columns
                raw = raw.loc[:, list(base.BOOK_TICKER_COLUMNS)]
                if not raw.empty:
                    yield raw
        finally:
            handle.close()
            archive.close()

    return members[0], iterator()


# The V1 probe resolves this module-level function dynamically, so replacing it retains checksum,
# quality and memory contracts while fixing only the cross-chunk schema boundary.
base._read_chunks = _read_chunks


def probe_bookticker(*, symbol: str, day: date, cache_dir: Path):
    result = base.probe_bookticker(symbol=symbol, day=day, cache_dir=cache_dir)
    result["probe_revision"] = PROBE_REVISION
    result["streaming_schema_contract"] = {
        "explicit_header_order_persists_across_chunks": True,
        "yielded_columns_reordered_to_canonical_schema": True,
        "canonical_schema": list(base.BOOK_TICKER_COLUMNS),
    }
    return result


def main() -> int:
    parser = base.argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--day", type=date.fromisoformat, default=date(2024, 4, 8))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe_bookticker(
        symbol=args.symbol,
        day=args.day,
        cache_dir=args.cache.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    base.write_json_atomic(args.output.resolve(), result)
    print(base.json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0 if result["usable_for_quote_resiliency_research"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
