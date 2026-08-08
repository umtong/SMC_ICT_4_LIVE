#!/usr/bin/env python3
"""Replace Candidate 13's daily kline transport with byte-equivalent monthly archives.

The trading state machine, timestamps, costs, fills, account rules and evaluation dates
are unchanged. Monthly files reduce requests; any missing or partial UTC day is restored
from the exact official daily archive and revalidated before the engine can run.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "source"
PATH = ROOT / "run_leadership_scdam.py"
text = PATH.read_text(encoding="utf-8")
if "candidate-09-monthly-transport-v3" in text:
    raise SystemExit(0)
start = text.index("def load_symbol_bars(\n")
end = text.index("\n\ndef _decimal", start)
replacement = '''def load_symbol_bars(
    symbol: str,
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """candidate-09-monthly-transport-v3: monthly first, exact daily gap repair."""
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = date(start.year, start.month, 1)
    last_month = date(end_inclusive.year, end_inclusive.month, 1)
    while cursor <= last_month:
        label = f"{cursor.year:04d}-{cursor.month:02d}"
        filename = f"{symbol}-1m-{label}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            f"{symbol}/1m/{filename}"
        )
        path = data_dir / symbol / "monthly" / filename
        _download(url, path)
        digest = sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != 1:
                raise RuntimeError(f"unexpected archive members: {filename}: {members}")
            member_bytes = archive.read(members[0])
            frame = pd.read_csv(BytesIO(member_bytes))
            if set(COLUMNS).issubset(frame.columns):
                frame = frame.loc[:, COLUMNS]
            else:
                frame = pd.read_csv(BytesIO(member_bytes), header=None, names=COLUMNS)
        numeric_open_time = pd.to_numeric(frame["open_time"], errors="coerce")
        frame = frame[numeric_open_time.notna()].copy()
        frame["open_time"] = pd.to_numeric(
            frame["open_time"],
            errors="raise",
        ).astype("int64")
        frames.append(frame)
        manifest.append({
            "symbol": symbol,
            "month": label,
            "url": url,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": digest,
            "rows": len(frame.index),
            "transport": "monthly-byte-equivalent",
        })
        cursor = date(
            cursor.year + (cursor.month == 12),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )

    def frame_to_result(raw: pd.DataFrame) -> pd.DataFrame:
        raw = raw.drop_duplicates(subset=["open_time"], keep="last").sort_values(
            "open_time",
            kind="stable",
        )
        first = int(raw["open_time"].iloc[0])
        if 1_000_000_000_000 <= first < 10_000_000_000_000:
            unit = "ms"
        elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
            unit = "us"
        else:
            raise RuntimeError(f"unsupported timestamp magnitude: {first}")
        index = pd.to_datetime(
            raw["open_time"],
            unit=unit,
            utc=True,
        ) + pd.Timedelta(minutes=1)
        result = pd.DataFrame(index=index)
        for name in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
            result[name] = pd.to_numeric(raw[name], errors="raise").to_numpy(copy=True)
        return result[~result.index.duplicated(keep="last")].sort_index()

    raw = pd.concat(frames, ignore_index=True)
    result = frame_to_result(raw)
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_inclusive + timedelta(days=1), tz="UTC")
    result = result[(result.index > lower) & (result.index <= upper)]

    expected_dates = pd.date_range(start, end_inclusive, freq="D", tz="UTC")
    counts = result.groupby(result.index.normalize()).size()
    problem_dates = [
        stamp
        for stamp in expected_dates
        if int(counts.get(stamp, 0)) not in (1439, 1440, 1441)
    ]
    for stamp in problem_dates:
        day = stamp.date()
        filename = f"{symbol}-1m-{day.isoformat()}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            f"{symbol}/1m/{filename}"
        )
        path = data_dir / symbol / "daily-fallback" / filename
        _download(url, path)
        digest = sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != 1:
                raise RuntimeError(f"unexpected daily archive members: {filename}: {members}")
            payload = archive.read(members[0])
            daily_raw = pd.read_csv(BytesIO(payload), header=None, names=COLUMNS)
        numeric_open_time = pd.to_numeric(daily_raw["open_time"], errors="coerce")
        daily_raw = daily_raw[numeric_open_time.notna()].copy()
        daily_raw["open_time"] = pd.to_numeric(
            daily_raw["open_time"],
            errors="raise",
        ).astype("int64")
        if len(daily_raw.index) not in (1439, 1440, 1441):
            raise RuntimeError(
                f"unexpected daily fallback row count {len(daily_raw.index)} for {filename}"
            )
        daily_result = frame_to_result(daily_raw)
        result = result[result.index.normalize() != stamp]
        result = pd.concat([result, daily_result]).sort_index()
        result = result[~result.index.duplicated(keep="last")]
        manifest.append({
            "symbol": symbol,
            "date": day.isoformat(),
            "url": url,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": digest,
            "rows": len(daily_raw.index),
            "transport": "exact-daily-gap-repair",
        })

    counts = result.groupby(result.index.normalize()).size()
    remaining = {
        stamp.date().isoformat(): int(counts.get(stamp, 0))
        for stamp in expected_dates
        if int(counts.get(stamp, 0)) not in (1439, 1440, 1441)
    }
    if remaining:
        raise RuntimeError(f"unresolved transport coverage for {symbol}: {remaining}")
    if not result.index.is_monotonic_increasing:
        raise RuntimeError(f"non-monotonic frame for {symbol}")
    return result, manifest
'''
PATH.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
