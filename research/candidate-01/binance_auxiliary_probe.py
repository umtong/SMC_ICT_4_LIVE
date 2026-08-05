#!/usr/bin/env python3
"""Probe official Binance Vision auxiliary futures archives and schemas."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile


URLS = {
    "metrics": "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2024-01-01.zip",
    "liquidation_snapshot": "https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2024-01-01.zip",
    "book_depth": "https://data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-2024-01-01.zip",
    "premium_index_1m": "https://data.binance.vision/data/futures/um/daily/premiumIndexKlines/BTCUSDT/1m/BTCUSDT-1m-2024-01-01.zip",
    "mark_price_1m": "https://data.binance.vision/data/futures/um/daily/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2024-01-01.zip",
    "funding_rate": "https://data.binance.vision/data/futures/um/daily/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01-01.zip",
}


def main() -> None:
    result: dict[str, object] = {}
    for name, url in URLS.items():
        try:
            request = Request(url, headers={"User-Agent": "smc-ict-4-research/1.0"})
            with urlopen(request, timeout=30) as response:
                payload = response.read()
                status = int(response.status)
            with ZipFile(BytesIO(payload)) as archive:
                members = archive.namelist()
                previews = {}
                for member in members[:3]:
                    text = archive.read(member).decode("utf-8", errors="replace")
                    previews[member] = text.splitlines()[:5]
            result[name] = {
                "available": True,
                "status": status,
                "url": url,
                "size_bytes": len(payload),
                "members": members,
                "previews": previews,
            }
        except (HTTPError, URLError, OSError, ValueError) as exc:
            result[name] = {
                "available": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
            }
    destination = Path("artifacts/candidate-01-auxiliary-probe")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "probe.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
