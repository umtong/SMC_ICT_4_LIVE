"""Reassemble and verify the immutable candidate-10 v26 source transport."""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import zipfile

ARCHIVE_SHA256 = "488af7a973c461506caa1d8312e3f7236c84ee039ccc80fbf28a6a6937c6ec20"
PARTS = (
    ("part-00.b64", 6000, "f221355d797d854cdad97b80c50caeec675a48e1bc5ed4b9c62c1d3d957bfa55"),
    ("part-01-0.b64", 2000, "0f7321437a5a33387e5b244b226a2a30654a9079a4c91550727f159abd1d6c51"),
    ("part-01-1.b64", 2000, "2c20fc18b49df73534a42e3d4cf86bbc4b04e4d1747a957b84fef9bb210b5904"),
    ("part-01-2.b64", 2000, "ad5536dc728f34409ce8b7649e3a68a3ddf9e9c7b91289faa285a637899ba04a"),
    ("part-02.b64", 6000, "fef7e9199954275a890542d1018427e939c12de66493e3ac5cb54231ba793879"),
    ("part-03.b64", 6000, "793ca6f4c31072918434fd0fbcf5e4717e895b2117f3ccd1e9a1dc93d74d7392"),
    ("part-04.b64", 4652, "f99efc1184f3c69781e78b9d7cbd63ff2502c66cfc8e0030904aaef2560b1045"),
)
MEMBER_HASHES = {
    "c10_v26_model.py": "254a3ca16b3736ac4a421c0073d8c8b69c6fee7ee2ffd116f1fdc0bd579384be",
    "c10_v26_research.py": "5d525e0b61fc73f743104f95197c845f4e34e0dc3b3308ab5477d7a656dc5f9a",
    "c10_v26_state.py": "50492a496f053ad2722f64cc14327a1d5a1452a39705392da085ed7304b4631f",
    "c10_v26_strategy.py": "fbdde454c71c59617236bd3862f890d338858ec8e56f667a9611e9d64fa068c6",
    "run_v26.py": "43889a7dd21092e8070e2c30b49d6b46286f2820f95ea7c566e31c6deffc11fd",
    "test_v26_install_order.py": "b4925bce5d435ec27c8dc373e05766994948d91e5f7af34b7fb3f11e0123116a",
    "test_v26_promotion.py": "c54d0e970533083e5b3695d721ac1df58c5ec6f2d5a34864e705f2a4ed725b97",
    "test_v26_research.py": "3e8c8965fb358e6d4c71ebcae8130c05d8021488ad1066c40643b21b597a8e29",
    "test_v26_state.py": "6fd755389372f1b615258ba60fb624740651e1a099484d31967c11ccdb9dff1a",
}


def materialize(parts_dir: Path, destination: Path, provenance: Path) -> None:
    encoded: list[str] = []
    part_records: list[dict[str, object]] = []
    for name, expected_length, expected_sha in PARTS:
        text = (parts_dir / name).read_text(encoding="ascii").strip()
        actual_sha = sha256(text.encode("ascii")).hexdigest()
        match = len(text) == expected_length and actual_sha == expected_sha
        part_records.append(
            {
                "name": name,
                "length": len(text),
                "expected_length": expected_length,
                "sha256": actual_sha,
                "expected_sha256": expected_sha,
                "match": match,
            },
        )
        if not match:
            raise RuntimeError(f"v26 source part mismatch: {name}")
        encoded.append(text)

    archive = base64.b64decode("".join(encoded), validate=True)
    archive_sha = sha256(archive).hexdigest()
    if archive_sha != ARCHIVE_SHA256:
        raise RuntimeError(
            f"v26 archive SHA mismatch: {archive_sha} != {ARCHIVE_SHA256}",
        )

    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination.parent / "v26_source.zip"
    archive_path.write_bytes(archive)
    actual_members: dict[str, str] = {}
    with zipfile.ZipFile(archive_path) as source:
        bad = source.testzip()
        if bad is not None:
            raise RuntimeError(f"corrupt v26 member: {bad}")
        names = sorted(name for name in source.namelist() if not name.endswith("/"))
        if set(names) != set(MEMBER_HASHES):
            raise RuntimeError(f"unexpected v26 members: {names}")
        for name in names:
            data = source.read(name)
            actual = sha256(data).hexdigest()
            expected = MEMBER_HASHES[name]
            if actual != expected:
                raise RuntimeError(
                    f"v26 member SHA mismatch: {name}: {actual} != {expected}",
                )
            (destination / name).write_bytes(data)
            actual_members[name] = actual

    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(
        json.dumps(
            {
                "transport": "ASCII base64 parts with per-part SHA256",
                "archive_bytes": len(archive),
                "archive_sha256": archive_sha,
                "parts": part_records,
                "members": actual_members,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts-dir", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    materialize(args.parts_dir, args.destination, args.provenance)


if __name__ == "__main__":
    main()
