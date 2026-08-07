"""Reassemble the immutable candidate-10 v21 source transport.

The research object is split into ASCII base64 files only because binary
contents-API writes corrupted the preceding v20 archives. Every logical part,
the decoded ZIP, and every UTF-8 source member is independently hashed before
execution.
"""
from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
from pathlib import Path
import zipfile

ARCHIVE_SHA256 = "c99539d58191618abea3b4376b2f5c19323de94bdbb2153be169b518f82c6f97"
PART_HASHES = {
    0: "52f41496da91a2fdf2806bdc9c6ebcda32455fc851ecf18dc1e2efec4310233b",
    1: "93b20226a6439efe11d4e1cd665e8373414c0eecd5e04a279e8b26141b7a39fb",
    2: "26bf469a09701717fc6c002b86e2f708a008b47e427de386332c3614ea0006a0",
    3: "6641dea88ca302d89c0fcbedacab154c41020cebdfb2884ccea3210518de79f8",
    4: "03be29f8746836139f026f69c5d22e2cef754a9574a0844d3ae5ac12fb0ab636",
    5: "f0f736e69c01617c93ddac74f93d789ea0fa3489ae6e2e6223f013591453e904",
    6: "352894613ad7d3b6deb216d3d38e21c44be15d17d43995221a645b82a811ea2a",
    7: "3e4cbc3a07333ccad44cf45cb22fd7e8eb99a489af47f33517bb8208cabdebf7",
    8: "c27aba22dfaf1543cd618e7e0f39761b76d971c1b32b30e378e75a7215d322a7",
    9: "24b42acc9803929f06539fea025a770ba5caedcea7f7030b5882e251ee42f0d2",
    10: "88e6627a5ccd5050db508b247e38e6d082ed405ff2168d661c7a3653763aaec7",
    11: "ce73ab14d9aaa063cd652a87f90da3978b723f2e994caebb7e66daaa2ab97003",
    12: "d99087760c3f7c6681a729845310e22460207c9cc5e82d00b15743bc1aa83033",
    13: "46fa11702a9877bb458f8b7de64be7aca5930096b7421c47abcf180cfde769de",
    14: "c63618325bfc2aa8cf8fab2770835d4b71ee1e2116be242040430d473f339683",
    15: "272cb5a33928203c69309011c8d6b0c8b9ac3aac3660bb5006ae4824ebbf0774",
    16: "2638221cc1b2f53c5ab299975e16370117d26c5a1dcd4a9c50ebe4283018e5a1",
    17: "eac310fd5479d7d450a709bae205f1404b443c399fa7fee71257f43c12cf8c00",
}
PART_FILES = (
    ("part-00.b64", (0,)),
    ("part-01.b64", (1,)),
    ("part-02.b64", (2,)),
    ("part-03.b64", (3,)),
    ("part-04-05.b64", (4, 5)),
    ("part-06-07.b64", (6, 7)),
    ("part-08-09.b64", (8, 9)),
    ("part-10-11.b64", (10, 11)),
    ("part-12-13.b64", (12, 13)),
    ("part-14-15.b64", (14, 15)),
    ("part-16-17.b64", (16, 17)),
)
MEMBER_HASHES = {
    "c10_liquidation_state.py": "8004b8acd2314378fa67522ecbb44eb62c97822000f74ca7bbfeb2a3ba4f3073",
    "c10_liquidation_strategy.py": "900ba381db2b66b617baf6976e332ce7572c5761a6df435f60301b6755c71d1c",
    "c10_liquidation_research.py": "0c550495610eabd44078ec1b149de226fef5030d1751517e899a00e5bdcec0c0",
    "run_research.py": "2b6df22d431a26257176b6614db56c3ba0f80f72739bba406b8f6de134e99467",
    "test_liquidation_state.py": "4b516ac1db4b026cbe91a4d2e59219f4aacd4894eb1fb5b68dee0e6f222a67bd",
}


def materialize(parts_dir: Path, destination: Path, provenance_path: Path) -> None:
    lengths = {index: (1184 if index == 17 else 1800) for index in range(18)}
    logical: dict[int, str] = {}
    diagnostics: list[dict[str, object]] = []
    for filename, indices in PART_FILES:
        text = (parts_dir / filename).read_text(encoding="ascii").strip()
        expected_length = sum(lengths[index] for index in indices)
        if len(text) != expected_length:
            raise RuntimeError(f"{filename} length {len(text)} != {expected_length}")
        cursor = 0
        for index in indices:
            segment = text[cursor:cursor + lengths[index]]
            cursor += lengths[index]
            actual = sha256(segment.encode("ascii")).hexdigest()
            expected = PART_HASHES[index]
            diagnostics.append({
                "file": filename,
                "logical_index": index,
                "length": len(segment),
                "sha256": actual,
                "expected_sha256": expected,
                "match": actual == expected,
            })
            if actual != expected:
                provenance_path.parent.mkdir(parents=True, exist_ok=True)
                provenance_path.write_text(
                    json.dumps({"logical_parts": diagnostics}, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                raise RuntimeError(f"logical source part {index:02d} SHA mismatch")
            logical[index] = segment

    archive = base64.b64decode("".join(logical[index] for index in range(18)), validate=True)
    archive_sha = sha256(archive).hexdigest()
    if archive_sha != ARCHIVE_SHA256:
        raise RuntimeError(f"archive SHA mismatch: {archive_sha} != {ARCHIVE_SHA256}")

    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination.parent / "v21_source.zip"
    archive_path.write_bytes(archive)
    actual_members: dict[str, str] = {}
    with zipfile.ZipFile(archive_path) as source:
        bad = source.testzip()
        if bad is not None:
            raise RuntimeError(f"corrupt member: {bad}")
        names = sorted(name for name in source.namelist() if not name.endswith("/"))
        if set(names) != set(MEMBER_HASHES):
            raise RuntimeError(f"unexpected members: {names}")
        for name in names:
            data = source.read(name)
            actual = sha256(data).hexdigest()
            expected = MEMBER_HASHES[name]
            if actual != expected:
                raise RuntimeError(f"member SHA mismatch: {name}: {actual} != {expected}")
            (destination / name).write_bytes(data)
            actual_members[name] = actual

    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(
        json.dumps({
            "source_archive_sha256": archive_sha,
            "source_archive_size_bytes": len(archive),
            "logical_parts": diagnostics,
            "members": actual_members,
            "transport": "ASCII base64 parts with per-logical-part SHA256",
        }, indent=2, sort_keys=True) + "\n",
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
