#!/usr/bin/env python3
"""Fail-fast official Binance Vision bookTicker availability probe."""
from __future__ import annotations
import json
import urllib.error
import urllib.request

DATES = [
    "2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22", "2024-01-29",
    "2024-02-05", "2024-02-12", "2024-02-19", "2024-02-26",
    "2024-03-04", "2024-03-11", "2024-03-18", "2024-03-25",
    "2024-04-01", "2024-04-08", "2024-04-15", "2024-04-22", "2024-04-29",
    "2024-05-06", "2024-05-13", "2024-05-20", "2024-05-27",
    "2024-06-03", "2024-06-10",
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
