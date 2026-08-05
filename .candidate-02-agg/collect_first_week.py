"""Collect only the prospectively locked v33 first BTC aggTrade week.

The strategy source and first-week lock were committed before this collector.
No later week is downloaded by this script.
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
DATA_START = date(2024, 7, 6)
DATA_END = date(2024, 7, 15)
ROOT = Path(".cache/candidate-02/v33-first-week-aggtrades")
BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"


def fetch(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 100:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    last: Exception | None = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "candidate-02-research/1.0"},
            )
            with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(temporary) as archive:
                members = [item for item in archive.namelist() if not item.endswith("/")]
                if len(members) != 1 or archive.getinfo(members[0]).file_size <= 0:
                    raise RuntimeError(f"invalid archive {url}")
            temporary.replace(path)
            return
        except Exception as exc:
            last = exc
            temporary.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"failed {url}: {last}")


def main() -> None:
    rows: list[dict[str, object]] = []
    current = DATA_START
    while current <= DATA_END:
        day = current.isoformat()
        name = f"{SYMBOL}-aggTrades-{day}.zip"
        url = f"{BASE}/{SYMBOL}/{name}"
        path = ROOT / name
        fetch(url, path)
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.namelist() if not item.endswith("/")]
            uncompressed_size = archive.getinfo(members[0]).file_size
        rows.append(
            {
                "date": day,
                "path": str(path),
                "archive_size": path.stat().st_size,
                "uncompressed_size": uncompressed_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "url": url,
            },
        )
        print(day, flush=True)
        current += timedelta(days=1)

    output = Path("artifacts/candidate-02-v33-first-week")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "candidate": "candidate-02-v33-confirmed-intraminute-inventory-handoff",
        "symbol": SYMBOL,
        "data_start": DATA_START.isoformat(),
        "data_end": DATA_END.isoformat(),
        "source": "Binance Vision USD-M daily aggTrades",
        "file_count": len(rows),
        "files": rows,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
