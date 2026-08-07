#!/usr/bin/env python3
"""Probe official Binance futures positioning/microstructure archives.

This is a temporary research probe. It does not trade, backtest, size positions,
or calculate PnL. It records only source availability and the first CSV rows so
that a causal data contract can be designed before any strategy change.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import csv
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
BASE = "https://data.binance.vision/data/futures/um/daily"
SYMBOL = "BTCUSDT"
DAY = date(2024, 10, 26).isoformat()
URLS = {
    "metrics": f"{BASE}/metrics/{SYMBOL}/{SYMBOL}-metrics-{DAY}.zip",
    "aggTrades": f"{BASE}/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{DAY}.zip",
    "trades": f"{BASE}/trades/{SYMBOL}/{SYMBOL}-trades-{DAY}.zip",
    "premiumIndexKlines": f"{BASE}/premiumIndexKlines/{SYMBOL}/1m/{SYMBOL}-1m-{DAY}.zip",
    "markPriceKlines": f"{BASE}/markPriceKlines/{SYMBOL}/1m/{SYMBOL}-1m-{DAY}.zip",
    "indexPriceKlines": f"{BASE}/indexPriceKlines/{SYMBOL}/1m/{SYMBOL}-1m-{DAY}.zip",
    "fundingRate": f"{BASE}/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{DAY}.zip",
}


def probe(name: str, url: str) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-11-positioning-probe"})
    try:
        with urlopen(request, timeout=60) as response:  # noqa: S310 fixed HTTPS host
            payload = response.read()
    except HTTPError as exc:
        return {"name": name, "url": url, "available": False, "http_status": exc.code}
    except URLError as exc:
        return {"name": name, "url": url, "available": False, "error": str(exc)}

    result: dict[str, object] = {
        "name": name,
        "url": url,
        "available": True,
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }
    try:
        with ZipFile(BytesIO(payload)) as archive:
            members = archive.namelist()
            result["members"] = members
            if len(members) != 1:
                result["schema_error"] = "archive must contain exactly one member"
                return result
            with archive.open(members[0]) as raw:
                reader = csv.reader(TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
                rows = []
                for _, row in zip(range(4), reader, strict=False):
                    rows.append(row)
                result["first_rows"] = rows
                result["column_count"] = max((len(row) for row in rows), default=0)
    except Exception as exc:  # schema discovery must preserve the exact failure
        result["schema_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> None:
    output = {
        "schema": "candidate-11-binance-positioning-probe-v1",
        "symbol": SYMBOL,
        "date": DAY,
        "sources": [probe(name, url) for name, url in URLS.items()],
    }
    path = ROOT / "positioning_data_probe.json"
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
