"""Join official Binance 5-minute positioning metrics to causal 1-minute features."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import urllib.request
from zipfile import ZipFile

import numpy as np
import pandas as pd

from spot_perp_features import load_range as load_spot_perp_range

METRICS_BASE = "https://data.binance.vision/data/futures/um/daily/metrics"


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _download(symbol: str, day: date, cache: Path) -> tuple[Path, Path, dict[str, object]]:
    name = f"{symbol}-metrics-{day.isoformat()}.zip"
    url = f"{METRICS_BASE}/{symbol}/{name}"
    directory = cache / "metrics"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / name
    checksum = directory / f"{name}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"metrics checksum mismatch: {archive}")
    return archive, checksum, {
        "endpoint": "usd_m_daily_metrics",
        "day": day.isoformat(),
        "archive": str(archive),
        "checksum": str(checksum),
        "size_bytes": archive.stat().st_size,
        "sha256": actual,
        "source_url": url,
        "role": "open interest and positioning state only",
    }


def _read_metrics(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected metrics archive members: {members}")
        frame = pd.read_csv(BytesIO(archive.read(members[0])))
    required = {
        "create_time",
        "sum_open_interest",
        "sum_open_interest_value",
        "sum_taker_long_short_vol_ratio",
    }
    if not required.issubset(frame.columns):
        raise RuntimeError(f"metrics schema drift: {sorted(frame.columns)}")
    raw_time = frame["create_time"]
    numeric = pd.to_numeric(raw_time, errors="coerce")
    if numeric.notna().all():
        median = float(numeric.median())
        unit = "s" if median < 100_000_000_000 else "ms"
        timestamp = pd.to_datetime(numeric, unit=unit, utc=True)
    else:
        timestamp = pd.to_datetime(raw_time, utc=True, errors="raise")
    result = pd.DataFrame(
        {
            "metrics_time_ns": timestamp.astype("datetime64[ns, UTC]").astype("int64"),
            "sum_open_interest": pd.to_numeric(frame["sum_open_interest"], errors="raise"),
            "sum_open_interest_value": pd.to_numeric(frame["sum_open_interest_value"], errors="raise"),
            "metrics_taker_ratio": pd.to_numeric(
                frame["sum_taker_long_short_vol_ratio"],
                errors="raise",
            ),
        },
    )
    return result.drop_duplicates("metrics_time_ns", keep="last").sort_values("metrics_time_ns")


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
):
    klines, feature_path, raw_files, evidence = load_spot_perp_range(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )
    frames: list[pd.DataFrame] = []
    metrics_evidence: list[dict[str, object]] = []
    day = start
    while day <= end:
        archive, checksum, item = _download(symbol, day, cache)
        frames.append(_read_metrics(archive))
        raw_files.extend([archive, checksum])
        metrics_evidence.append(item)
        day += timedelta(days=1)
    metrics = pd.concat(frames, ignore_index=True).drop_duplicates(
        "metrics_time_ns",
        keep="last",
    ).sort_values("metrics_time_ns")
    metrics["oi_change_5m"] = metrics["sum_open_interest"].pct_change()
    metrics["oi_value_change_5m"] = metrics["sum_open_interest_value"].pct_change()

    base = pd.read_csv(feature_path, compression="infer")
    base["feature_time_ns"] = pd.to_numeric(
        base["observed_time_ns"],
        errors="raise",
    ).astype("int64")
    merged = pd.merge_asof(
        base.sort_values("feature_time_ns"),
        metrics,
        left_on="feature_time_ns",
        right_on="metrics_time_ns",
        direction="backward",
        allow_exact_matches=True,
        tolerance=5 * 60 * 1_000_000_000,
    ).sort_index()
    merged["positioning_feature_ready"] = merged[
        [
            "sum_open_interest",
            "sum_open_interest_value",
            "metrics_taker_ratio",
            "oi_change_5m",
            "oi_value_change_5m",
        ]
    ].replace([np.inf, -np.inf], np.nan).notna().all(axis=1)
    if int(merged["positioning_feature_ready"].sum()) == 0:
        raise RuntimeError("positioning metrics did not reach the feature clock")
    base_ready = merged["feature_ready"].astype(str).str.lower().isin({"true", "1", "yes"})
    merged["feature_ready"] = base_ready & merged["positioning_feature_ready"]
    merged.to_csv(feature_path, index=False, compression="gzip")

    raw_path = output / "raw_evidence.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw.extend(metrics_evidence)
    raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return klines, feature_path, raw_files, evidence


__all__ = ["load_range"]
