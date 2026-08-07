#!/usr/bin/env python3
"""Verify V45 protected sources against the exact frozen GitHub commit.

The Git metadata directory is not reliably visible inside every Actions job
container. This verifier therefore compares repository bytes against public raw
content at the predeclared commit. It never modifies strategy or evidence files.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import time
import urllib.error
import urllib.request

REPOSITORY = "umtong/SMC_ICT_4_LIVE"


def download(url: str, attempts: int = 5) -> bytes:
    error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "candidate-04-v45-freeze-verifier"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (OSError, urllib.error.URLError) as exc:
            error = exc
            if attempt < attempts:
                time.sleep(attempt)
    raise RuntimeError(f"failed to download frozen source after {attempts} attempts: {url}: {error}")


def validate_relative(path_text: str) -> Path:
    pure = PurePosixPath(path_text)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError(f"unsafe protected path: {path_text}")
    return Path(*pure.parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--freeze",
        type=Path,
        default=Path("research/candidate-04/freeze-v45.json"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.repository_root.resolve()
    freeze_path = args.freeze if args.freeze.is_absolute() else root / args.freeze
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    commit = str(freeze["frozen_source_commit"])
    protected = [str(value) for value in freeze["protected_paths"]]
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise SystemExit(f"invalid frozen commit: {commit}")
    if not protected:
        raise SystemExit("freeze has no protected paths")

    rows: list[dict[str, object]] = []
    mismatches: list[str] = []
    for path_text in protected:
        relative = validate_relative(path_text)
        local_path = root / relative
        if not local_path.is_file():
            mismatches.append(f"missing local protected file: {path_text}")
            continue
        local = local_path.read_bytes()
        url = f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{relative.as_posix()}"
        frozen = download(url)
        local_digest = sha256(local).hexdigest()
        frozen_digest = sha256(frozen).hexdigest()
        matched = local == frozen
        rows.append(
            {
                "path": relative.as_posix(),
                "matched": matched,
                "local_sha256": local_digest,
                "frozen_sha256": frozen_digest,
                "size_bytes": len(local),
            }
        )
        if not matched:
            mismatches.append(
                f"protected source changed: {path_text}: {local_digest} != {frozen_digest}"
            )

    evidence = {
        "repository": REPOSITORY,
        "frozen_source_commit": commit,
        "protected_file_count": len(protected),
        "verified_file_count": len(rows),
        "all_bytes_match": not mismatches and len(rows) == len(protected),
        "mismatches": mismatches,
        "files": rows,
    }
    if args.output:
        output = args.output if args.output.is_absolute() else root / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    if not evidence["all_bytes_match"]:
        raise SystemExit("V45 frozen-source verification failed")


if __name__ == "__main__":
    main()
