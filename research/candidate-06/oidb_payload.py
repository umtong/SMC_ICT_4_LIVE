# -*- coding: utf-8 -*-
"""Materialize the predeclared OIDB source bundle from ASCII chunks."""
from __future__ import annotations
import base64
import io
from pathlib import Path
import tarfile

def main() -> None:
    root = Path(__file__).resolve().parent
    chunks = sorted(root.glob("oidb_payload.chunk-*"))
    if not chunks:
        raise RuntimeError("OIDB payload chunks missing")
    encoded = "".join(path.read_text(encoding="ascii").strip() for path in chunks)
    raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive.getmembers():
            destination = (root / member.name).resolve()
            if root not in destination.parents:
                raise RuntimeError(f"unsafe payload member: {member.name}")
        archive.extractall(root, filter="data")
    print(f"OIDB source bundle materialized from {len(chunks)} chunks")

if __name__ == "__main__":
    main()
