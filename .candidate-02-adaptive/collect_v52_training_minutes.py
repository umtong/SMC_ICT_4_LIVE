"""Collect official Binance USD-M one-minute bars for causal barrier training.

The files cover only the already selected v48/v52 development history through
its first BTC discovery week. They are used to train path-consistent historical
labels; future fresh sequential weeks remain uncollected.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json, time, urllib.request, zipfile

START = date(2024, 9, 1)
END = date(2025, 3, 10)
SYMBOL = "BTCUSDT"
ROOT = Path(".cache/candidate-02/v52-training-minutes")
OUT = Path("artifacts/candidate-02-v52-training-minutes")
BASE = "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m"
WORKERS = 16


def fetch(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 100:
        return
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


def task(day: date) -> dict:
    ds = day.isoformat()
    name = f"{SYMBOL}-1m-{ds}.zip"
    url = f"{BASE}/{name}"
    path = ROOT / name
    fetch(url, path)
    return {"date": ds, "path": str(path), "size": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest(), "url": url}


def main() -> None:
    days = []
    day = START
    while day <= END:
        days.append(day)
        day += timedelta(days=1)
    files = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(task, day): day for day in days}
        for future in as_completed(futures):
            item = future.result()
            files.append(item)
            print(item["date"], flush=True)
    files.sort(key=lambda item: item["date"])
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"symbol": SYMBOL, "start": START.isoformat(), "end": END.isoformat(), "workers": WORKERS, "file_count": len(files), "files": files}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
