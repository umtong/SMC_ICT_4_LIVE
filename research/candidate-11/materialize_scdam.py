#!/usr/bin/env python3
"""Fail-closed materializer for Candidate 11's deterministic SCDAM runtime."""
from __future__ import annotations

from base64 import b64decode
from hashlib import sha256
from io import BytesIO
import json
import lzma
from pathlib import Path, PurePosixPath
import tarfile

RUNTIME_SHA256 = "a83f7781b4b96185ab4fb8530a3f4e849f308836adda41ad6176f13fbc7480e7"
RUNTIME_SCHEMA = "candidate-11-scdam-runtime-v1"
EXPECTED_PARTS = tuple(f"scdam_runtime.part{i:02d}.b64" for i in range(12))


def _runtime_bytes(root: Path) -> bytes:
    paths = tuple(root / name for name in EXPECTED_PARTS)
    present = tuple(path.name for path in paths if path.exists())
    if not present:
        required = (root / "session_engine.py", root / "logic.py", root / "run.py")
        if all(path.exists() for path in required):
            print("SCDAM runtime is already materialized")
            raise SystemExit(0)
        raise SystemExit("SCDAM runtime chunks and materialized source are both missing")
    if present != EXPECTED_PARTS:
        missing = sorted(set(EXPECTED_PARTS) - set(present))
        raise SystemExit(f"incomplete SCDAM runtime chunk set: missing={missing}")
    encoded = "".join(path.read_text(encoding="ascii") for path in paths)
    try:
        payload = b64decode(encoded, validate=True)
    except Exception as exc:
        raise SystemExit(f"invalid SCDAM runtime base64: {exc}") from exc
    actual = sha256(payload).hexdigest()
    if actual != RUNTIME_SHA256:
        raise SystemExit(
            f"SCDAM runtime SHA-256 mismatch: expected={RUNTIME_SHA256} actual={actual}",
        )
    return payload


def _safe_file_name(name: str) -> bool:
    pure = PurePosixPath(name)
    return not pure.is_absolute() and ".." not in pure.parts and len(pure.parts) == 1


def main() -> None:
    root = Path(__file__).resolve().parent
    compressed = _runtime_bytes(root)
    try:
        tar_bytes = lzma.decompress(compressed, format=lzma.FORMAT_XZ)
    except lzma.LZMAError as exc:
        raise SystemExit(f"SCDAM runtime XZ failure: {exc}") from exc

    with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if "RUNTIME_MANIFEST.json" not in names:
            raise SystemExit("SCDAM runtime manifest missing")
        if len(names) != len(set(names)):
            raise SystemExit("duplicate SCDAM runtime member")
        for member in members:
            if not member.isfile() or not _safe_file_name(member.name):
                raise SystemExit(f"unsafe SCDAM runtime member: {member.name}")
        manifest_file = archive.extractfile("RUNTIME_MANIFEST.json")
        if manifest_file is None:
            raise SystemExit("SCDAM runtime manifest is unreadable")
        manifest = json.loads(manifest_file.read())
        if manifest.get("schema") != RUNTIME_SCHEMA:
            raise SystemExit("unsupported SCDAM runtime schema")
        expected_files = manifest.get("files") or {}
        if set(names) - {"RUNTIME_MANIFEST.json"} != set(expected_files):
            raise SystemExit("SCDAM runtime member set does not match manifest")
        for name, record in expected_files.items():
            if not _safe_file_name(name):
                raise SystemExit(f"unsafe manifest member: {name}")
            source = archive.extractfile(name)
            if source is None:
                raise SystemExit(f"SCDAM runtime member is unreadable: {name}")
            payload = source.read()
            if len(payload) != int(record["size_bytes"]):
                raise SystemExit(f"SCDAM runtime size mismatch: {name}")
            if sha256(payload).hexdigest() != record["sha256"]:
                raise SystemExit(f"SCDAM runtime content hash mismatch: {name}")
            temporary = root / f".{name}.tmp"
            temporary.write_bytes(payload)
            temporary.replace(root / name)

    stale_paths = [
        root / "scdam_source_bundle.zip",
        root / "scdam_source_bundle.rebuilt.zip",
        root / "v3_source_bundle.zip",
        root / "validation_trigger.txt",
        root / "scdam_source_bundle.part00.bin.b64",
    ]
    stale_paths.extend(root.glob("scdam_source_bundle.part*.b64"))
    stale_paths.extend(root.glob("scdam_runtime.part*.b64"))
    for stale in stale_paths:
        stale.unlink(missing_ok=True)
    print(f"materialized {len(expected_files)} verified SCDAM runtime files")


if __name__ == "__main__":
    main()
