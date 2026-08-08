#!/usr/bin/env python3
"""Use byte-equivalent Binance monthly klines for the frozen Candidate 12 runner."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "source"
PATH = ROOT / "data_loader.py"
text = PATH.read_text(encoding="utf-8")
if "candidate-09-c12-monthly-transport-v1" in text:
    raise SystemExit(0)
text = text.replace(
    "from hashlib import sha256\n",
    "from hashlib import sha256\nfrom io import BytesIO\n",
    1,
)
start = text.index("def load_binance_bars(\n")
replacement = '''def load_binance_bars(
    symbol: str,
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """candidate-09-c12-monthly-transport-v1: same causal rows, fewer requests."""
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = date(start.year, start.month, 1)
    final_month = date(end_inclusive.year, end_inclusive.month, 1)
    while cursor <= final_month:
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
            if len(members) != 1 or not members[0].lower().endswith(".csv"):
                raise RuntimeError(f"unexpected ZIP members in {filename}: {members}")
            payload = archive.read(members[0])
            frame = pd.read_csv(BytesIO(payload))
            if set(COLUMNS).issubset(frame.columns):
                frame = frame.loc[:, COLUMNS]
            else:
                frame = pd.read_csv(BytesIO(payload), header=None, names=COLUMNS)
        numeric_open_time = pd.to_numeric(frame["open_time"], errors="coerce")
        frame = frame[numeric_open_time.notna()].copy()
        frame["open_time"] = pd.to_numeric(
            frame["open_time"],
            errors="raise",
        ).astype("int64")
        frames.append(frame)
        manifest.append(
            {
                "symbol": symbol,
                "month": label,
                "url": url,
                "file": f"{symbol}/monthly/{filename}",
                "bytes": path.stat().st_size,
                "sha256": digest,
                "rows": len(frame.index),
                "transport": "monthly-byte-equivalent",
            },
        )
        cursor = date(
            cursor.year + (cursor.month == 12),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["open_time"], keep="last")
    raw = raw.sort_values("open_time", kind="stable").reset_index(drop=True)
    open_time = raw["open_time"]
    first = int(open_time.iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported Binance timestamp magnitude: {first}")
    index = pd.to_datetime(open_time, unit=unit, utc=True) + pd.Timedelta(minutes=1)
    values: dict[str, Any] = {}
    for name in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        values[name] = pd.to_numeric(raw[name], errors="raise").to_numpy(copy=True)
    result = pd.DataFrame(values, index=index)
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_inclusive + timedelta(days=1), tz="UTC")
    result = result[(result.index > lower) & (result.index <= upper)]
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise RuntimeError("market-data timestamps are not strictly increasing and unique")
    if (result["volume"] < 0).any() or (result["taker_buy_volume"] < 0).any():
        raise RuntimeError("negative volume in source data")
    if (result["taker_buy_volume"] > result["volume"] + 1e-9).any():
        raise RuntimeError("taker-buy volume exceeds total volume")
    expected_min = ((end_inclusive - start).days + 1) * 1439
    if len(result.index) < expected_min:
        raise RuntimeError(
            f"monthly transport coverage too short for {symbol}: "
            f"{len(result.index)} < {expected_min}"
        )
    return result, manifest
'''
PATH.write_text(text[:start] + replacement, encoding="utf-8")
