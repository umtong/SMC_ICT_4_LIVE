#!/usr/bin/env python3
"""Materialize the predeclared UOAM source bundle from ASCII-safe parts."""
from __future__ import annotations
import base64, json, zlib
from pathlib import Path

def main() -> int:
    here = Path(__file__).resolve().parent
    parts = sorted(here.glob("uoam_payload.part-*"))
    if not parts:
        raise RuntimeError("UOAM payload parts missing")
    encoded = "".join(p.read_text(encoding="ascii").strip() for p in parts)
    payload = json.loads(zlib.decompress(base64.b64decode(encoded)).decode("utf-8"))
    for name, value in payload.items():
        destination = here / name
        destination.write_bytes(base64.b64decode(value))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
