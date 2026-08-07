"""Collect only the prospectively locked v55 first BTC week.

This downloads immutable Binance Vision one-minute archives after the candidate,
parameters, risk and week were committed. It never computes trading outcomes.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json
import time
import urllib.request
import zipfile

SYMBOL = "BTCUSDT"
EVALUATION_START = date(2024, 11, 18)
DATA_START = EVALUATION_START - timedelta(days=2)
DATA_END = EVALUATION_START + timedelta(days=7)
ROOT = Path("inputs/v55-first-week/.cache/candidate-02/v55-first-week/binance_1m")
OUT = Path("inputs/v55-first-week/artifacts/candidate-02-v55-first-week-data")
BASE = "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m"


def download(day: date) -> dict[str, object]:
    value = day.isoformat()
    name = f"{SYMBOL}-1m-{value}.zip"
    url = f"{BASE}/{name}"
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    last: Exception | None = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(temporary) as archive:
                members = [name for name in archive.namelist() if not name.endswith("/")]
                if len(members) != 1 or archive.getinfo(members[0]).file_size <= 0:
                    raise RuntimeError(f"invalid archive: {url}")
            temporary.replace(path)
            return {
                "date": value,
                "url": url,
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        except Exception as exc:
            last = exc
            temporary.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(min(20, 2**attempt))
    raise RuntimeError(f"failed {url}: {last}")


def main() -> None:
    days = []
    value = DATA_START
    while value <= DATA_END:
        days.append(value)
        value += timedelta(days=1)
    records = []
    with ThreadPoolExecutor(max_workers=min(8, len(days))) as executor:
        futures = {executor.submit(download, day): day for day in days}
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            print(record["date"], flush=True)
    records.sort(key=lambda item: str(item["date"]))
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "candidate": "candidate-02-frozen-auction-acceptance-rejection-v55",
        "symbol": SYMBOL,
        "evaluation_start": EVALUATION_START.isoformat(),
        "data_start": DATA_START.isoformat(),
        "data_end_inclusive": DATA_END.isoformat(),
        "file_count": len(records),
        "files": records,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (OUT / "STATUS_READY_FOR_LOCKED_FIRST_WEEK_NAUTILUSTRADER").write_text(
        EVALUATION_START.isoformat() + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
