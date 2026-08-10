"""Recollect only the already revealed v75 BTC one-minute bars for v76."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import time
import urllib.request
import zipfile

SYMBOL = "BTCUSDT"
START = date(2024, 9, 14)
END = date(2024, 9, 23)
OUT = Path("inputs/v76-first-week/.cache/candidate-02/v76-first-week/binance_1m")
BASE = "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m"


def fetch(day: date) -> None:
    name = f"{SYMBOL}-1m-{day.isoformat()}.zip"
    url = f"{BASE}/{name}"
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    temp = path.with_suffix(".zip.tmp")
    last: Exception | None = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(req, timeout=120) as response, temp.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(temp) as archive:
                members = [item for item in archive.namelist() if not item.endswith("/")]
                if len(members) != 1 or archive.getinfo(members[0]).file_size <= 0:
                    raise RuntimeError(f"invalid archive {url}")
            temp.replace(path)
            return
        except Exception as exc:
            last = exc
            temp.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(min(16, 2**attempt))
    raise RuntimeError(f"failed {url}: {last}")


def main() -> None:
    current = START
    count = 0
    while current <= END:
        fetch(current)
        count += 1
        current += timedelta(days=1)
    if count != 10 or len(list(OUT.glob("BTCUSDT-1m-*.zip"))) != 10:
        raise RuntimeError("expected ten one-minute archives")


if __name__ == "__main__":
    main()
