"""Verify and materialize the immutable candidate-10 v26 source archive."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import zipfile

ARCHIVE_SHA256 = "93d82395bf24ea0d7c94e237e6f60dfe755bf92e3bc87be22c6e39484780eb00"
MEMBER_HASHES = {
    "c10_v26_model.py": "8b07c032bd698c5274889b81851a70157c3372ae8933f1c15f01dc9cfed25a95",
    "c10_v26_state.py": "50492a496f053ad2722f64cc14327a1d5a1452a39705392da085ed7304b4631f",
    "c10_v26_strategy.py": "fbdde454c71c59617236bd3862f890d338858ec8e56f667a9611e9d64fa068c6",
    "c10_v26_research.py": "8432a9e4917849990c2bd76934c2b78a0a0026a292199f01cfdf476444b235f6",
    "run_v26.py": "7c3bff25dd8ff84b551dba6c6cbbe15a7cb47fd01d3cbe2321976843822ce8ad",
    "test_v26_state.py": "6fd755389372f1b615258ba60fb624740651e1a099484d31967c11ccdb9dff1a",
    "test_v26_research.py": "3e8c8965fb358e6d4c71ebcae8130c05d8021488ad1066c40643b21b597a8e29",
    "test_v26_install_order.py": "b4925bce5d435ec27c8dc373e05766994948d91e5f7af34b7fb3f11e0123116a",
}


def materialize(archive: Path, destination: Path, provenance: Path) -> None:
    data = archive.read_bytes()
    archive_sha = sha256(data).hexdigest()
    if archive_sha != ARCHIVE_SHA256:
        raise RuntimeError(
            f"v26 archive SHA mismatch: {archive_sha} != {ARCHIVE_SHA256}",
        )
    destination.mkdir(parents=True, exist_ok=True)
    actual: dict[str, str] = {}
    with zipfile.ZipFile(archive) as source:
        bad = source.testzip()
        if bad is not None:
            raise RuntimeError(f"corrupt v26 member: {bad}")
        names = sorted(name for name in source.namelist() if not name.endswith("/"))
        if set(names) != set(MEMBER_HASHES):
            raise RuntimeError(f"unexpected v26 members: {names}")
        for name in names:
            member = source.read(name)
            digest = sha256(member).hexdigest()
            expected = MEMBER_HASHES[name]
            if digest != expected:
                raise RuntimeError(
                    f"v26 member SHA mismatch: {name}: {digest} != {expected}",
                )
            (destination / name).write_bytes(member)
            actual[name] = digest
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(
        json.dumps(
            {
                "archive": str(archive),
                "archive_bytes": len(data),
                "archive_sha256": archive_sha,
                "members": actual,
                "contract": (
                    "immutable source archive; archive and every member verified "
                    "before NautilusTrader execution"
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    materialize(args.archive, args.destination, args.provenance)


if __name__ == "__main__":
    main()
