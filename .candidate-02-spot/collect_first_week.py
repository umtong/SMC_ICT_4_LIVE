"""Collect only the first BTC spot screen week fixed for candidate-02.

No second or third week is downloaded before the first week passes.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json
import time
import urllib.request
import zipfile

SYMBOL = "BTCUSDT"
EVALUATION_START = date(2024, 7, 8)
DATA_START = EVALUATION_START - timedelta(days=2)
DATA_END = EVALUATION_START + timedelta(days=7)
ROOT = Path(".cache/candidate-02/spot-first-week")
BASE = "https://data.binance.vision/data/spot/daily/klines"


def fetch(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    last: Exception | None = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(req, timeout=90) as response, tmp.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(tmp) as archive:
                members = [item for item in archive.namelist() if not item.endswith("/")]
                if len(members) != 1 or archive.getinfo(members[0]).file_size <= 0:
                    raise RuntimeError(f"invalid archive {url}")
            tmp.replace(path)
            return
        except Exception as exc:
            last = exc
            tmp.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(min(20, 2 ** attempt))
    raise RuntimeError(f"failed {url}: {last}")


def main() -> None:
    rows = []
    current = DATA_START
    while current <= DATA_END:
        day = current.isoformat()
        name = f"{SYMBOL}-1m-{day}.zip"
        url = f"{BASE}/{SYMBOL}/1m/{name}"
        path = ROOT / name
        fetch(url, path)
        rows.append({
            "date": day,
            "path": str(path),
            "size": path.stat().st_size,
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "url": url,
        })
        print(day, flush=True)
        current += timedelta(days=1)
    output = Path("artifacts/candidate-02-spot-first")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "symbol": SYMBOL,
        "evaluation_start": EVALUATION_START.isoformat(),
        "data_start": DATA_START.isoformat(),
        "data_end": DATA_END.isoformat(),
        "source": "Binance Vision spot daily klines",
        "files": rows,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
