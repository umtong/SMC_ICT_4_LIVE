"""Adapt the immutable v75 direct-data transformer for pre-bookDepth v90 weeks.

The locked v90 state machine does not use depth as an entry gate.  The only
reference is a score multiplier used to order same-timestamp signals, while
v90 emits mutually exclusive one-signal-per-timestamp states.  Binance Vision
has no USD-M ``bookDepth`` archive for 2022-02-14, so this adapter removes that
unavailable source and creates explicit neutral depth columns.  It does not
create orders, fills, positions, PnL or NAV and does not alter any v90 trading
condition, direction, price level, cost or risk parameter.
"""
from __future__ import annotations

import argparse
from pathlib import Path


COLLECTOR_BOOK_LINE = (
    '    "bookDepth": "https://data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT",\n'
)

OLD_MAIN_BLOCK = '''    agg_frames = [aggregate_trades(path) for path in sorted(AGG.glob("BTCUSDT-aggTrades-*.zip"))]
    book_frames = [aggregate_book(path) for path in sorted(BOOK.glob("BTCUSDT-bookDepth-*.zip"))]
    if len(agg_frames) != 10 or len(book_frames) != 10:
        raise ValueError("expected ten daily direct-data archives per source")
    trades = pd.concat(agg_frames).sort_index()
    book = pd.concat(book_frames).sort_index()
    if trades.index.has_duplicates or book.index.has_duplicates:
        raise ValueError("duplicate minute features")
    raw = load_raw_one_minute(RAW)
    data = raw[["open", "high", "low", "close", "volume"]].join(trades, how="left").join(book, how="left")
'''

NEW_MAIN_BLOCK = '''    agg_frames = [aggregate_trades(path) for path in sorted(AGG.glob("BTCUSDT-aggTrades-*.zip"))]
    if len(agg_frames) != 10:
        raise ValueError("expected ten daily aggTrade archives")
    trades = pd.concat(agg_frames).sort_index()
    if trades.index.has_duplicates:
        raise ValueError("duplicate minute trade features")
    raw = load_raw_one_minute(RAW)
    data = raw[["open", "high", "low", "close", "volume"]].join(trades, how="left")
    # Binance Vision does not publish USD-M bookDepth for this locked 2022 week.
    # Neutral values are explicit missing-source markers, not inferred market data.
    # v90 does not gate on these fields; they only make a neutral score multiplier.
    neutral_depth_columns = (
        "bid_depth_1pct_first",
        "bid_depth_1pct_end",
        "bid_depth_1pct_mean",
        "ask_depth_1pct_first",
        "ask_depth_1pct_end",
        "ask_depth_1pct_mean",
        "book_snapshot_count",
        "bid_depth_change_1m",
        "ask_depth_change_1m",
        "book_imbalance_end",
    )
    for column in neutral_depth_columns:
        data[column] = 0.0
'''


def adapt(root: Path) -> dict[str, str]:
    collectors = sorted(root.glob("collect*.py"))
    builders = sorted(root.glob("build*.py"))
    if len(collectors) != 1 or len(builders) != 1:
        raise RuntimeError(
            f"expected one collector and one builder under {root}: "
            f"collectors={collectors}, builders={builders}"
        )
    collector = collectors[0]
    builder = builders[0]

    collector_text = collector.read_text(encoding="utf-8")
    if COLLECTOR_BOOK_LINE not in collector_text:
        raise RuntimeError("immutable collector no longer contains the expected bookDepth source")
    collector_text = collector_text.replace(COLLECTOR_BOOK_LINE, "", 1)
    if '"bookDepth":' in collector_text:
        raise RuntimeError("bookDepth source remains in adapted collector")
    collector.write_text(collector_text, encoding="utf-8")

    builder_text = builder.read_text(encoding="utf-8")
    if OLD_MAIN_BLOCK not in builder_text:
        raise RuntimeError("immutable feature builder no longer matches the controlled patch point")
    builder_text = builder_text.replace(OLD_MAIN_BLOCK, NEW_MAIN_BLOCK, 1)
    if "book_frames =" in builder_text or ".join(book, how=" in builder_text:
        raise RuntimeError("unavailable bookDepth data remains required by adapted builder")
    builder.write_text(builder_text, encoding="utf-8")

    return {"collector": str(collector), "builder": str(builder)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    paths = adapt(args.root)
    print(paths)


if __name__ == "__main__":
    main()
