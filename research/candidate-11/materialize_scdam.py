#!/usr/bin/env python3
"""Fail-closed materializer for the committed Candidate 11 SCDAM source bundle."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

BUNDLE_SHA256 = "9492652cabb57d19cec8f15208d9366be9e0bef87aa7447a8e10dfa515e9e4aa"
BUNDLE_SCHEMA = "candidate-11-scdam-source-bundle-v1"


def main() -> None:
    root = Path(__file__).resolve().parent
    bundle = root / "scdam_source_bundle.zip"
    if not bundle.exists():
        required = root / "session_engine.py"
        if required.exists():
            print("SCDAM source is already materialized")
            return
        raise SystemExit("SCDAM bundle and materialized source are both missing")

    actual = sha256(bundle.read_bytes()).hexdigest()
    if actual != BUNDLE_SHA256:
        raise SystemExit(f"source bundle SHA-256 mismatch: expected={BUNDLE_SHA256} actual={actual}")

    with ZipFile(bundle) as archive:
        names = archive.namelist()
        if "BUNDLE_MANIFEST.json" not in names:
            raise SystemExit("bundle manifest missing")
        manifest = json.loads(archive.read("BUNDLE_MANIFEST.json"))
        if manifest.get("schema") != BUNDLE_SCHEMA:
            raise SystemExit("unsupported SCDAM bundle schema")
        expected_files = manifest.get("files") or {}
        archive_files = set(names) - {"BUNDLE_MANIFEST.json"}
        if archive_files != set(expected_files):
            raise SystemExit("bundle member set does not match manifest")
        for name, record in expected_files.items():
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 1:
                raise SystemExit(f"unsafe bundle member: {name}")
            payload = archive.read(name)
            if len(payload) != int(record["size_bytes"]):
                raise SystemExit(f"bundle size mismatch: {name}")
            if sha256(payload).hexdigest() != record["sha256"]:
                raise SystemExit(f"bundle content hash mismatch: {name}")
            (root / name).write_bytes(payload)

    bundle.unlink()
    for stale in (root / "v3_source_bundle.zip", root / "validation_trigger.txt"):
        stale.unlink(missing_ok=True)
    print(f"materialized {len(expected_files)} verified SCDAM files")


if __name__ == "__main__":
    main()
