"""Probe official Binance Vision USD-M archive availability and size.

This diagnostic deliberately performs no strategy calculation. It verifies which
historical microstructure streams exist before candidate-09 commits to another
large data build.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://data.binance.vision/data/futures/um/daily"


def inspect_url(url: str) -> dict[str, Any]:
    result: dict[str, Any] = {"url": url, "exists": False}
    request = Request(url, method="HEAD", headers={"User-Agent": "SMC-ICT-4-candidate-09/0.2"})
    try:
        with urlopen(request, timeout=60) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            result.update(
                {
                    "exists": 200 <= response.status < 300,
                    "status": int(response.status),
                    "content_length": int(headers["content-length"]) if headers.get("content-length", "").isdigit() else None,
                    "content_type": headers.get("content-type"),
                    "etag": headers.get("etag"),
                    "last_modified": headers.get("last-modified"),
                }
            )
    except HTTPError as exc:
        result.update({"status": int(exc.code), "error": str(exc)})
    except (URLError, TimeoutError) as exc:
        result.update({"status": None, "error": str(exc)})
    return result


def main() -> int:
    symbol = "BTCUSDT"
    day = "2024-10-14"
    data_types = ["bookTicker", "bookDepth", "liquidationSnapshot", "metrics"]
    probes: list[dict[str, Any]] = []
    for data_type in data_types:
        filename = f"{symbol}-{data_type}-{day}.zip"
        url = f"{BASE}/{data_type}/{symbol}/{filename}"
        probes.append({"data_type": data_type, **inspect_url(url)})
        probes.append({"data_type": f"{data_type}.CHECKSUM", **inspect_url(f"{url}.CHECKSUM")})

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "date": day,
        "probes": probes,
    }
    destination = Path("artifacts/candidate-09-market-data-probe")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "probe.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
