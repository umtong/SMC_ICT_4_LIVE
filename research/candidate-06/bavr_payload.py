#!/usr/bin/env python3
# -*- coding: ascii -*-
"""Materialize the predeclared candidate-06 BAVR source bundle."""
from __future__ import annotations
import base64
import io
from pathlib import Path
import tarfile


def main() -> int:
    root = Path(__file__).resolve().parent
    parts = sorted(root.glob("bavr_payload.chunk-*"))
    if not parts:
        raise RuntimeError("BAVR payload chunks are missing")
    encoded = "".join(part.read_text(encoding="ascii") for part in parts)
    raw = base64.b64decode(encoded, validate=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            target = Path(member.name)
            if member.isdir() or target.is_absolute() or ".." in target.parts or len(target.parts) != 1:
                raise RuntimeError(f"unsafe BAVR payload member: {member.name}")
        bundle.extractall(root, filter="data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
