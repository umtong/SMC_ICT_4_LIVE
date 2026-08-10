"""Probe direct USD-M microstructure archives for one fixed BTC development day."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import urllib.error
import urllib.request
import zipfile

SYMBOL = "BTCUSDT"
DAY = "2024-07-08"
KINDS = ("bookDepth", "bookTicker", "liquidationSnapshot", "aggTrades", "trades")
ROOT = Path(".cache/candidate-02/micro-probe")
BASE = "https://data.binance.vision/data/futures/um/daily"


def attempt(kind: str) -> dict:
    name = f"{SYMBOL}-{kind}-{DAY}.zip"
    url = f"{BASE}/{kind}/{SYMBOL}/{name}"
    path = ROOT / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as stream:
            while chunk := response.read(1 << 20):
                stream.write(chunk)
        with zipfile.ZipFile(path) as archive:
            members = [member for member in archive.namelist() if not member.endswith("/")]
            member_sizes = {member: archive.getinfo(member).file_size for member in members}
            first_lines = {}
            for member in members[:1]:
                with archive.open(member) as source:
                    first_lines[member] = [source.readline().decode("utf-8", errors="replace").strip() for _ in range(3)]
        return {
            "kind": kind,
            "available": True,
            "url": url,
            "path": str(path),
            "archive_size": path.stat().st_size,
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "members": member_sizes,
            "first_lines": first_lines,
        }
    except urllib.error.HTTPError as exc:
        path.unlink(missing_ok=True)
        return {"kind": kind, "available": False, "url": url, "http_status": exc.code}
    except Exception as exc:
        path.unlink(missing_ok=True)
        return {"kind": kind, "available": False, "url": url, "error": repr(exc)}


def main() -> None:
    results = [attempt(kind) for kind in KINDS]
    output = Path("artifacts/candidate-02-micro-probe")
    output.mkdir(parents=True, exist_ok=True)
    (output / "probe.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    main()
