"""Collect immutable USD-M positioning and basis data for candidate-02.

The six weeks were fixed before these files were downloaded. The collector uses
Binance Vision only, records SHA-256 for every available archive, and records a
404 as an explicit availability fact rather than silently fabricating data.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json
import time
import urllib.error
import urllib.request
import zipfile

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
WEEKS = ("2024-12-23", "2022-04-25", "2024-07-08", "2025-08-25", "2023-11-27", "2021-04-19")
ROOT = Path(".cache/candidate-02/derivative-state")
BASE = "https://data.binance.vision/data/futures/um"


def fetch(url: str, path: Path) -> tuple[bool, str | None]:
    """Return availability and an optional error without inventing missing rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 50:
        return True, None
    temporary = path.with_suffix(path.suffix + ".tmp")
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "candidate-02-research/1.0"},
            )
            with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(temporary) as archive:
                members = [name for name in archive.namelist() if not name.endswith("/")]
                if len(members) != 1 or archive.getinfo(members[0]).file_size <= 0:
                    raise RuntimeError(f"invalid archive {url}")
            temporary.replace(path)
            return True, None
        except urllib.error.HTTPError as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if exc.code == 404:
                return False, "HTTP_404_NOT_AVAILABLE"
        except Exception as exc:  # network, archive and filesystem errors
            last_error = exc
            temporary.unlink(missing_ok=True)
        if attempt < 5:
            time.sleep(min(20, 2**attempt))
    return False, f"DOWNLOAD_FAILED:{type(last_error).__name__}:{last_error}"


def record_available(kind: str, symbol: str, key: str, path: Path, url: str) -> dict[str, object]:
    return {
        "kind": kind,
        "symbol": symbol,
        "key": key,
        "available": True,
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "url": url,
    }


def record_missing(kind: str, symbol: str, key: str, url: str, reason: str | None) -> dict[str, object]:
    return {
        "kind": kind,
        "symbol": symbol,
        "key": key,
        "available": False,
        "reason": reason,
        "url": url,
    }


def collect(rows: list[dict[str, object]], kind: str, symbol: str, key: str, url: str, path: Path) -> None:
    available, reason = fetch(url, path)
    if available:
        rows.append(record_available(kind, symbol, key, path, url))
        print("available", kind, symbol, key, flush=True)
    else:
        rows.append(record_missing(kind, symbol, key, url, reason))
        print("missing", kind, symbol, key, reason, flush=True)


def main() -> None:
    days: set[date] = set()
    months: set[tuple[int, int]] = set()
    for week in WEEKS:
        start = date.fromisoformat(week) - timedelta(days=2)
        end = date.fromisoformat(week) + timedelta(days=7)
        day = start
        while day <= end:
            days.add(day)
            months.add((day.year, day.month))
            day += timedelta(days=1)

    rows: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        for day in sorted(days):
            day_string = day.isoformat()
            metrics_name = f"{symbol}-metrics-{day_string}.zip"
            metrics_url = f"{BASE}/daily/metrics/{symbol}/{metrics_name}"
            collect(rows, "metrics", symbol, day_string, metrics_url, ROOT / "metrics" / symbol / metrics_name)

            premium_name = f"{symbol}-1m-{day_string}.zip"
            premium_url = f"{BASE}/daily/premiumIndexKlines/{symbol}/1m/{premium_name}"
            collect(
                rows,
                "premiumIndexKlines",
                symbol,
                day_string,
                premium_url,
                ROOT / "premiumIndexKlines" / symbol / premium_name,
            )

        for year, month in sorted(months):
            month_string = f"{year:04d}-{month:02d}"
            funding_name = f"{symbol}-fundingRate-{month_string}.zip"
            funding_url = f"{BASE}/monthly/fundingRate/{symbol}/{funding_name}"
            collect(
                rows,
                "fundingRate",
                symbol,
                month_string,
                funding_url,
                ROOT / "fundingRate" / symbol / funding_name,
            )

    output = Path("artifacts/candidate-02-state")
    output.mkdir(parents=True, exist_ok=True)
    available = [row for row in rows if row["available"]]
    missing = [row for row in rows if not row["available"]]
    manifest = {
        "source": "Binance Vision USD-M",
        "symbols": SYMBOLS,
        "weeks": WEEKS,
        "requested_file_count": len(rows),
        "available_file_count": len(available),
        "missing_file_count": len(missing),
        "missing_is_not_imputed": True,
        "files": rows,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # BTC is the common market-leverage state required for the first analysis.
    btc_metrics = [
        row for row in available if row["symbol"] == "BTCUSDT" and row["kind"] == "metrics"
    ]
    if len(btc_metrics) < len(days):
        raise RuntimeError(f"incomplete BTC metrics: {len(btc_metrics)}/{len(days)}")


if __name__ == "__main__":
    main()
