"""Collect only the prospectively locked third BTC week's positioning metrics.

The v46 state machine and two-week evidence were committed before this collector
was permitted to reveal the 2022-08-22 confirmation week.
"""
from __future__ import annotations
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json, time, urllib.request, zipfile

SYMBOL = "BTCUSDT"
START = date(2022, 8, 20)
END = date(2022, 8, 29)
ROOT = Path(".cache/candidate-02/v46-week-three-metrics")
OUT = Path("artifacts/candidate-02-v46-week-three-metrics")
BASE = "https://data.binance.vision/data/futures/um/daily/metrics"

def fetch(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    last = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response, tmp.open("wb") as stream:
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

def main() -> None:
    files = []
    day = START
    while day <= END:
        ds = day.isoformat()
        name = f"{SYMBOL}-metrics-{ds}.zip"
        url = f"{BASE}/{SYMBOL}/{name}"
        path = ROOT / name
        fetch(url, path)
        files.append({"date": ds, "path": str(path), "size": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest(), "url": url})
        print(ds, flush=True)
        day += timedelta(days=1)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"candidate": "informed_inventory_buildup_continuation_v46", "locked_week": "2022-08-22", "collection_start": START.isoformat(), "collection_end": END.isoformat(), "files": files}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
