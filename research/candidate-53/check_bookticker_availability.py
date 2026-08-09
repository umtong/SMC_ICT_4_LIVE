#!/usr/bin/env python3
"""Fail-fast official Binance Vision bookTicker availability probe."""
from __future__ import annotations
import json
import urllib.error
import urllib.request

DATES = [
    "2024-01-08", "2024-06-10", "2025-01-06", "2025-03-07",
    "2025-06-23", "2025-12-08", "2026-01-05", "2026-02-09",
    "2026-04-06", "2026-06-01", "2026-07-01", "2026-07-22",
]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
BASE = "https://data.binance.vision/data/futures/um/daily/bookTicker"

out = {}
for symbol in SYMBOLS:
    rows = {}
    for day in DATES:
        url = f"{BASE}/{symbol}/{symbol}-bookTicker-{day}.zip.CHECKSUM"
        req = urllib.request.Request(url, headers={"User-Agent":"SMC-ICT-4-research"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                rows[day] = {"status": int(response.status), "text": response.read(100).decode("utf-8", errors="replace")}
        except urllib.error.HTTPError as exc:
            rows[day] = {"status": int(exc.code)}
        except Exception as exc:
            rows[day] = {"status": "ERROR", "error": repr(exc)}
    out[symbol] = rows
print(json.dumps(out, indent=2, sort_keys=True))
