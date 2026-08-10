"""Collect prospectively locked BTC spot aggTrades and one-minute klines for v89."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import time
import urllib.request
import zipfile

LOCK = Path("research/candidate-02/v89_cross_market_impact_lock.json")
ROOT = Path("inputs/v89-first-week/spot")
SYMBOL = "BTCUSDT"
SOURCES = {
    "aggTrades": f"https://data.binance.vision/data/spot/daily/aggTrades/{SYMBOL}",
    "klines": f"https://data.binance.vision/data/spot/daily/klines/{SYMBOL}/1m",
}


def target(kind: str, day: date) -> tuple[str, Path]:
    token = day.isoformat()
    name = f"{SYMBOL}-1m-{token}.zip" if kind == "klines" else f"{SYMBOL}-aggTrades-{token}.zip"
    return f"{SOURCES[kind]}/{name}", ROOT / kind / name


def fetch(kind: str, day: date) -> dict[str, object]:
    url, path = target(kind, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    last: Exception | None = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(temporary) as archive:
                members = [name for name in archive.namelist() if not name.endswith("/")]
                if len(members) != 1 or archive.getinfo(members[0]).file_size <= 0:
                    raise RuntimeError(f"invalid archive {url}")
            temporary.replace(path)
            return {
                "kind": kind,
                "date": day.isoformat(),
                "path": str(path),
                "url": url,
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
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if lock["status"] != "LOCKED_BEFORE_FIRST_WEEK_COLLECTION":
        raise RuntimeError("v89 first-week lock is not prospective")
    evaluation_start = date.fromisoformat(lock["first_week"]["start_utc"][:10])
    data_start = evaluation_start - timedelta(days=2)
    data_end = evaluation_start + timedelta(days=7)
    days: list[date] = []
    current = data_start
    while current <= data_end:
        days.append(current)
        current += timedelta(days=1)
    jobs = [(kind, day) for kind in SOURCES for day in days]
    records: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, *job): job for job in jobs}
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            print(record["kind"], record["date"], flush=True)
    records.sort(key=lambda value: (str(value["kind"]), str(value["date"])))
    counts = {kind: sum(record["kind"] == kind for record in records) for kind in SOURCES}
    if any(counts[kind] != len(days) for kind in SOURCES):
        raise RuntimeError(f"incomplete v89 spot data: {counts}")
    output = Path("inputs/v89-first-week/artifacts/candidate-02-v89-first-week-data")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "candidate_family": lock["candidate_family"],
        "symbol": SYMBOL,
        "evaluation_start": evaluation_start.isoformat(),
        "data_start": data_start.isoformat(),
        "data_end_inclusive": data_end.isoformat(),
        "sources": SOURCES,
        "counts": counts,
        "files": records,
    }
    (output / "spot_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
