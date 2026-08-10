"""Collect the prospectively locked v46 confirmation week.

Metrics are downloaded and validated before any price archive is requested. This
prevents an unavailable feature week from silently becoming a zero-signal test.
"""
from __future__ import annotations
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import csv, io, json, time, urllib.request, zipfile

SYMBOL = "BTCUSDT"
WEEK = "2025-02-17"
START = date(2025, 2, 15)
END = date(2025, 2, 24)
ROOT = Path(".cache/candidate-02/v46-confirmation")
OUT = Path("artifacts/candidate-02-v46-confirmation")
BASE = "https://data.binance.vision/data/futures/um/daily"
REQUIRED = (
    "sum_open_interest",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)

def fetch(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    last = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(tmp) as archive:
                members = [name for name in archive.namelist() if not name.endswith("/")]
                if len(members) != 1 or archive.getinfo(members[0]).file_size <= 0:
                    raise RuntimeError(f"invalid archive: {url}")
            tmp.replace(path)
            return
        except Exception as exc:
            last = exc
            tmp.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(min(20, 2**attempt))
    raise RuntimeError(f"failed {url}: {last}")

def metadata(kind: str, day: str, path: Path, url: str) -> dict:
    return {"kind": kind, "date": day, "path": str(path), "size": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest(), "url": url}

def metrics_available(path: Path) -> bool:
    with zipfile.ZipFile(path) as archive:
        member = [name for name in archive.namelist() if not name.endswith("/")][0]
        text = io.TextIOWrapper(archive.open(member), encoding="utf-8", newline="")
        reader = csv.DictReader(text)
        rows = list(reader)
    if not rows or any(field not in reader.fieldnames for field in REQUIRED):
        return False
    for field in REQUIRED:
        values = [row.get(field, "").strip() for row in rows]
        if not any(value not in {"", "nan", "NaN", "NULL", "null"} for value in values):
            return False
    return True

def main() -> None:
    files = []
    metric_paths = []
    day = START
    while day <= END:
        ds = day.isoformat()
        name = f"{SYMBOL}-metrics-{ds}.zip"
        url = f"{BASE}/metrics/{SYMBOL}/{name}"
        path = ROOT / "metrics" / name
        fetch(url, path)
        files.append(metadata("metrics", ds, path, url))
        metric_paths.append(path)
        print("metrics", ds, flush=True)
        day += timedelta(days=1)
    if not all(metrics_available(path) for path in metric_paths):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "STATUS_FEATURES_UNAVAILABLE").write_text(WEEK + "\n", encoding="utf-8")
        (OUT / "manifest.json").write_text(json.dumps({"candidate": "informed_inventory_buildup_continuation_v46", "week": WEEK, "feature_complete": False, "files": files}, indent=2), encoding="utf-8")
        return
    day = START
    while day <= END:
        ds = day.isoformat()
        name = f"{SYMBOL}-1m-{ds}.zip"
        url = f"{BASE}/klines/{SYMBOL}/1m/{name}"
        path = ROOT / "klines" / name
        fetch(url, path)
        files.append(metadata("klines", ds, path, url))
        print("klines", ds, flush=True)
        day += timedelta(days=1)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"candidate": "informed_inventory_buildup_continuation_v46", "week": WEEK, "feature_complete": True, "files": files}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (OUT / "STATUS_READY_FOR_LOCKED_EVALUATION").write_text(WEEK + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
