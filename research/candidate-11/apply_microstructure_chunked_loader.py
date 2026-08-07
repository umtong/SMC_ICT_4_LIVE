#!/usr/bin/env python3
"""Replace daily aggTrades parsing with bounded-memory chunk aggregation."""
from __future__ import annotations

from pathlib import Path

MARKER = "C11_MICRO_CHUNKED_DAILY_AGGREGATION"

FUNCTION = r'''def aggregate_day(path: Path) -> pd.DataFrame:
    # C11_MICRO_CHUNKED_DAILY_AGGREGATION: preserve exchange row order and
    # reduce each bounded chunk to one-second partial bars before concatenation.
    partials: list[pd.DataFrame] = []
    with ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members for {path.name}: {members}")
        member = members[0]
        with archive.open(member) as probe:
            first_line = probe.readline().decode("utf-8", errors="replace").strip().lower()
        has_header = "agg_trade" in first_line or "transact_time" in first_line
        with archive.open(member) as stream:
            reader = pd.read_csv(
                stream,
                header=0 if has_header else None,
                names=None if has_header else AGG_COLUMNS,
                usecols=list(AGG_COLUMNS) if has_header else range(len(AGG_COLUMNS)),
                chunksize=500_000,
                low_memory=False,
            )
            for chunk_sequence, frame in enumerate(reader):
                if has_header:
                    missing = set(AGG_COLUMNS) - set(frame.columns)
                    if missing:
                        raise RuntimeError(f"aggTrades schema missing columns: {sorted(missing)}")
                    frame = frame.loc[:, AGG_COLUMNS].copy()
                else:
                    frame.columns = AGG_COLUMNS
                for name in ("price", "quantity", "transact_time"):
                    frame[name] = pd.to_numeric(frame[name], errors="coerce")
                frame = frame.dropna(subset=["price", "quantity", "transact_time"])
                frame = frame[(frame["price"] > 0) & (frame["quantity"] > 0)]
                if frame.empty:
                    continue
                unit = timestamp_unit(int(frame["transact_time"].iloc[0]))
                frame["second"] = pd.to_datetime(
                    frame["transact_time"].astype("int64"),
                    unit=unit,
                    utc=True,
                ).dt.floor("s")
                buyer_maker = parse_bool(frame["is_buyer_maker"])
                frame["quote"] = frame["price"] * frame["quantity"]
                frame["buy_volume"] = frame["quantity"].where(~buyer_maker, 0.0)
                frame["sell_volume"] = frame["quantity"].where(buyer_maker, 0.0)
                frame["signed_notional"] = frame["quote"].where(~buyer_maker, -frame["quote"])
                frame["trade_notional"] = frame["quote"]
                grouped = frame.groupby("second", sort=True, observed=True)
                partial = grouped.agg(
                    open=("price", "first"),
                    high=("price", "max"),
                    low=("price", "min"),
                    close=("price", "last"),
                    volume=("quantity", "sum"),
                    buy_volume=("buy_volume", "sum"),
                    sell_volume=("sell_volume", "sum"),
                    quote_notional=("quote", "sum"),
                    signed_notional=("signed_notional", "sum"),
                    trade_count=("price", "size"),
                    max_trade_notional=("trade_notional", "max"),
                )
                partial["_chunk_sequence"] = chunk_sequence
                partials.append(partial.reset_index())
    if not partials:
        raise RuntimeError(f"empty aggregate-trade archive: {path.name}")
    combined = pd.concat(partials, ignore_index=True)
    combined = combined.sort_values(["second", "_chunk_sequence"], kind="stable")
    grouped = combined.groupby("second", sort=True, observed=True)
    result = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        buy_volume=("buy_volume", "sum"),
        sell_volume=("sell_volume", "sum"),
        quote_notional=("quote_notional", "sum"),
        signed_notional=("signed_notional", "sum"),
        trade_count=("trade_count", "sum"),
        max_trade_notional=("max_trade_notional", "max"),
    )
    result.index = pd.DatetimeIndex(result.index, tz="UTC") + pd.Timedelta(seconds=1)
    return result


'''


def apply(root: Path) -> int:
    path = root / "run_microstructure_nautilus.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return 0
    start = source.find("def aggregate_day(path: Path) -> pd.DataFrame:\n")
    end = source.find("def load_one_second_bars(\n", start)
    if start < 0 or end < 0:
        raise SystemExit("aggregate_day replacement anchors missing")
    path.write_text(source[:start] + FUNCTION + source[end:], encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"microstructure chunked loader applied: {apply(root)}")


if __name__ == "__main__":
    main()
