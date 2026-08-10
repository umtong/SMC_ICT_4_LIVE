"""Collect only the prospectively locked second BTC week for v34.

The candidate rules were committed before this file is allowed to reveal week-two
book depth or aggregate trades.
"""
from __future__ import annotations
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json, time, urllib.request, zipfile

SYMBOL = "BTCUSDT"
START = date(2025, 8, 2)
END = date(2025, 8, 11)
ROOT = Path(".cache/candidate-02/v34-week-two")
OUT = Path("artifacts/candidate-02-v34-week-two")
BASE = "https://data.binance.vision/data/futures/um/daily"
KINDS = {
    "aggTrades": lambda ds: f"{SYMBOL}-aggTrades-{ds}.zip",
    "bookDepth": lambda ds: f"{SYMBOL}-bookDepth-{ds}.zip",
}

def fetch(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(req, timeout=120) as response, tmp.open("wb") as stream:
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
    rows = []
    day = START
    while day <= END:
        ds = day.isoformat()
        for kind, naming in KINDS.items():
            name = naming(ds)
            url = f"{BASE}/{kind}/{SYMBOL}/{name}"
            path = ROOT / kind / name
            fetch(url, path)
            rows.append({"kind": kind, "date": ds, "path": str(path), "size": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest(), "url": url})
            print(kind, ds, flush=True)
        day += timedelta(days=1)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"candidate": "depth_vacuum_breakout_hold_v34", "week": "2025-08-04", "collection_start": START.isoformat(), "collection_end": END.isoformat(), "files": rows}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
