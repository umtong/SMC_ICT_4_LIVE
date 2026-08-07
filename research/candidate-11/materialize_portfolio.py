#!/usr/bin/env python3
"""Fail-closed materializer for the four-market independent SCDAM runner."""
from __future__ import annotations

from base64 import b64decode
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
import lzma
import tarfile

ARCHIVE_SHA256 = "263f8a7ba7c8dd22f0fe2ac3d5c5f5163f3622b610e3e2b23b12ff8eb66483cf"
PARTS = ("portfolio_runtime.part00.b64", "portfolio_runtime.part01.b64")
FILES = {
    "run_portfolio_scdam.py": (37894, "ec53b1f11afc48e76c54a3c3a83183895106442bb0ae0e4eab24860cb1797e8a"),
    "test_portfolio_scdam.py": (2714, "61b45e61cee30d9079ac7c6198c325526c1b73e8fe998403f2fd569f0e4554d3"),
}


def _safe(name: str) -> bool:
    pure = PurePosixPath(name)
    return not pure.is_absolute() and ".." not in pure.parts and len(pure.parts) == 1


def main() -> None:
    root = Path(__file__).resolve().parent
    part_paths = tuple(root / name for name in PARTS)
    present = tuple(path.name for path in part_paths if path.exists())
    if not present:
        missing = [name for name in FILES if not (root / name).is_file()]
        if missing:
            raise SystemExit(f"portfolio source parts and materialized files are missing: {missing}")
        print("portfolio SCDAM source already materialized")
        return
    if present != PARTS:
        raise SystemExit(f"incomplete portfolio source part set: {present}")

    encoded = "".join(path.read_text(encoding="ascii") for path in part_paths)
    try:
        payload = b64decode(encoded, validate=True)
    except Exception as exc:
        raise SystemExit(f"invalid portfolio source base64: {exc}") from exc
    actual_archive = sha256(payload).hexdigest()
    if actual_archive != ARCHIVE_SHA256:
        raise SystemExit(
            f"portfolio source archive hash mismatch: expected={ARCHIVE_SHA256} actual={actual_archive}",
        )

    try:
        tar_bytes = lzma.decompress(payload, format=lzma.FORMAT_XZ)
    except lzma.LZMAError as exc:
        raise SystemExit(f"portfolio source XZ failure: {exc}") from exc
    try:
        with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)) or set(names) != set(FILES):
                raise SystemExit(f"portfolio source member set mismatch: {names}")
            for member in members:
                if not member.isfile() or not _safe(member.name):
                    raise SystemExit(f"unsafe portfolio source member: {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise SystemExit(f"unreadable portfolio source member: {member.name}")
                data = source.read()
                expected_size, expected_hash = FILES[member.name]
                if len(data) != expected_size or sha256(data).hexdigest() != expected_hash:
                    raise SystemExit(f"portfolio source content mismatch: {member.name}")
                temporary = root / f".{member.name}.tmp"
                temporary.write_bytes(data)
                temporary.replace(root / member.name)
    except tarfile.TarError as exc:
        raise SystemExit(f"portfolio source tar failure: {exc}") from exc

    for path in part_paths:
        path.unlink()
    print(f"materialized {len(FILES)} verified portfolio SCDAM files")


if __name__ == "__main__":
    main()
