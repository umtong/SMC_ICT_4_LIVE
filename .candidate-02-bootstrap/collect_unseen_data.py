"""Collect the prospectively locked BTC holdout for candidate-02.

The exact weeks and rule were committed in ``locked_rule_v2.json`` before this
collector was changed. This helper only downloads immutable Binance Vision
files; it does not inspect outcomes or alter parameters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path("research/candidate-02").resolve()))

from backtest import load_binance_1m  # noqa: E402


LOCK_PATH = Path("research/candidate-02/locked_rule_v2.json")


def main() -> None:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    weeks = tuple(lock["research_basis"]["holdout_weeks"])
    if len(weeks) != 30 or len(set(weeks)) != 30:
        raise RuntimeError("prospective holdout lock must contain 30 unique weeks")

    cache = Path(".cache/candidate-02/binance-vision")
    for value in weeks:
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
