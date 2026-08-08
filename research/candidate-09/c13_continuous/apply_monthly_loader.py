#!/usr/bin/env python3
"""Replace Candidate 13's daily kline transport with byte-equivalent monthly archives.

The trading state machine, timestamps, costs, fills, account rules and evaluation dates
are unchanged. This patch only reduces thousands of HTTP requests to monthly files.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "source"
PATH = ROOT / "run_leadership_scdam.py"
text = PATH.read_text(encoding="utf-8")
if "candidate-09-monthly-transport-v1" in text:
    raise SystemExit(0)
start = text.index("def load_symbol_bars(\n")
end = text.index("\n\ndef _decimal", start)
replacement = '''def load_symbol_bars(
    symbol: str,
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """candidate-09-monthly-transport-v1: same rows, fewer HTTP requests."""
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
        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()
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

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["open_time"], keep="last").sort_values(
        "open_time",
        kind="stable",
    )
    first = int(pd.to_numeric(raw["open_time"], errors="raise").iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported timestamp magnitude: {first}")
    index = pd.to_datetime(
        pd.to_numeric(raw["open_time"], errors="raise"),
        unit=unit,
        utc=True,
    ) + pd.Timedelta(minutes=1)
    result = pd.DataFrame(index=index)
    for name in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        result[name] = pd.to_numeric(raw[name], errors="raise").to_numpy(copy=True)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_inclusive + timedelta(days=1), tz="UTC")
    result = result[(result.index > lower) & (result.index <= upper)]
    expected_min = int((end_inclusive - start).days + 1) * 1439
    if len(result.index) < expected_min:
        raise RuntimeError(
            f"monthly transport coverage too short for {symbol}: "
            f"{len(result.index)} < {expected_min}"
        )
    if not result.index.is_monotonic_increasing:
        raise RuntimeError(f"non-monotonic frame for {symbol}")
    return result, manifest
'''
PATH.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
