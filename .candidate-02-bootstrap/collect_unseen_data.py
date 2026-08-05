"""Collect deterministic unseen BTC windows for candidate-02 falsification.

This bootstrap helper only downloads immutable Binance Vision files.  It does
not inspect outcomes or alter strategy parameters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path("research/candidate-02").resolve()))

from backtest import load_binance_1m  # noqa: E402


LOCKED_UNSEEN_MONDAYS = (
    "2025-02-10",
    "2024-08-26",
    "2024-09-02",
    "2025-04-28",
    "2023-05-15",
    "2022-01-10",
    "2022-09-05",
    "2022-10-03",
    "2023-03-20",
)


def main() -> None:
    cache = Path(".cache/candidate-02/binance-vision")
    for value in LOCKED_UNSEEN_MONDAYS:
        start = datetime.fromisoformat(value).replace(tzinfo=UTC)
        load_binance_1m(
            "BTCUSDT",
            start=start - timedelta(days=2),
            end=start + timedelta(days=7, hours=1),
            cache_root=cache,
        )
        print(value, flush=True)


if __name__ == "__main__":
    main()
