"""Collect only the prospectively locked v53 second BTC week.

The week, candidate code, parameters, risk and promotion criteria were committed
before this collector. This module downloads immutable Binance Vision archives;
it does not calculate signals, fills or performance.
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
EVALUATION_START = date(2024, 12, 30)
DATA_START = EVALUATION_START - timedelta(days=2)
DATA_END = EVALUATION_START + timedelta(days=7)  # inclusive archive for +1h exit buffer
ROOT = Path("inputs/v53-second-week/.cache/candidate-02/v53-second-week/binance_1m")
OUT = Path("inputs/v53-second-week/artifacts/candidate-02-v53-second-week-data")
BASE = "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m"


def _download(day: date) -> dict[str, object]:
    value = day.isoformat()
    name = f"{SYMBOL}-1m-{value}.zip"
    url = f"{BASE}/{name}"
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(temporary) as archive:
                members = [item for item in archive.namelist() if not item.endswith("/")]
                if len(members) != 1:
                    raise RuntimeError(f"unexpected archive members: {members}")
                info = archive.getinfo(members[0])
                if info.file_size <= 0:
                    raise RuntimeError("empty CSV member")
                with archive.open(members[0]) as source:
                    first = source.readline()
                    if not first:
                        raise RuntimeError("empty CSV")
            temporary.replace(path)
            return {
                "date": value,
                "url": url,
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        except Exception as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(min(20, 2**attempt))
    raise RuntimeError(f"failed {url}: {last_error}")


def main() -> None:
    days: list[date] = []
    value = DATA_START
    while value <= DATA_END:
        days.append(value)
        value += timedelta(days=1)
    records: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(days))) as executor:
        future_to_day = {executor.submit(_download, day): day for day in days}
        for future in as_completed(future_to_day):
            record = future.result()
            records.append(record)
            print(record["date"], flush=True)
    records.sort(key=lambda item: str(item["date"]))
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "candidate": "candidate-02-full-auction-rotation-v53-nautilustrader",
        "purpose": "prospectively locked second BTC week data only",
        "symbol": SYMBOL,
        "evaluation_start": EVALUATION_START.isoformat(),
        "data_start": DATA_START.isoformat(),
        "data_end_inclusive": DATA_END.isoformat(),
        "file_count": len(records),
        "files": records,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (OUT / "STATUS_READY_FOR_LOCKED_SECOND_WEEK_NAUTILUSTRADER").write_text(
        EVALUATION_START.isoformat() + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"file_count": len(records), "evaluation_start": EVALUATION_START.isoformat()}))


if __name__ == "__main__":
    main()
