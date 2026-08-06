#!/usr/bin/env python3
"""Reconstruct the exact OLAR source bundle committed as small payload parts.

The packaging exists only to move a multi-file UTF-8 change through a connector
with small write limits. It is not a backtest or research engine. The script
verifies the compressed payload and every extracted file before writing.
"""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
import tarfile


PAYLOAD_SHA256 = "5c99a8c03634fb3e1d6226312e54ecec9cb2db5b15ab4a89d2731c3c975e3ba6"
FILE_SHA256 = {
    "OBJECTIVE_LIFECYCLE_RESEARCH_LEDGER.md": "0453a9f39d76042eb3d7600653eafb9ebb7ab41de787deb31ee43ae7e8a7623c",
    "config.olar.json": "9b642b3f96db61a2f2782311de60de66fecbe265fd5edec83795db31d83b0536",
    "objective_lifecycle_core.py": "9c4067baa327cf00d8a7b9fc4f5338ec7339d315be7712f543d39469aceaacf4",
    "objective_lifecycle_engine.py": "00179e14c076f8666aa39419cadd634d0c7c05fdf612f6f0d93782915a1498df",
    "register_objective_lifecycle_engine.py": "dab72a5cda0442dfef1b8c19e4b86ceef589001db2178d62b07bd2d1a6d1f473",
    "run_objective_lifecycle_matrix.py": "3d3ef9f1a36805cdbb931af9297af3a9b295be973cefb57d650e28f73f1ffdb9",
    "test_objective_lifecycle_core.py": "c2e2600e6113fd301ce481c172698f1553ff6223e8d7b15ed31152f2659038b0",
    "test_objective_lifecycle_engine.py": "f166ed288aafd467deeb3acdf0b1db39cc3a3e6b11f0acbff804371af05e8ef6",
    "test_register_objective_lifecycle_engine.py": "24ab8f986bf07b99d3be37b44ace51124c69ef712aee30dddf27a45c2bfb96a5",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reconstruct(candidate_dir: Path) -> tuple[Path, ...]:
    parts = sorted(candidate_dir.glob("olar_payload.part-*"))
    if not parts:
        raise RuntimeError("no OLAR payload parts found")
    encoded = b"".join(path.read_bytes().strip() for path in parts)
    try:
        payload = base64.b64decode(encoded, validate=True)
    except Exception as exc:  # pragma: no cover - exact exception varies by Python
        raise RuntimeError(f"invalid OLAR base64 payload: {exc}") from exc
    actual_payload_sha = _sha256(payload)
    if actual_payload_sha != PAYLOAD_SHA256:
        raise RuntimeError(
            f"OLAR payload checksum mismatch: {actual_payload_sha} != {PAYLOAD_SHA256}",
        )

    extracted: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if names != set(FILE_SHA256):
            raise RuntimeError(
                f"OLAR payload file set mismatch: {sorted(names)} != {sorted(FILE_SHA256)}",
            )
        for member in members:
            if not member.isfile() or member.name.startswith("/") or ".." in Path(member.name).parts:
                raise RuntimeError(f"unsafe OLAR payload member: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"unable to read OLAR payload member: {member.name}")
            data = source.read()
            expected = FILE_SHA256[member.name]
            actual = _sha256(data)
            if actual != expected:
                raise RuntimeError(
                    f"OLAR file checksum mismatch for {member.name}: {actual} != {expected}",
                )
            extracted[member.name] = data

    written: list[Path] = []
    for name in sorted(extracted):
        destination = candidate_dir / name
        data = extracted[name]
        if destination.exists():
            existing_sha = _sha256(destination.read_bytes())
            if existing_sha != FILE_SHA256[name]:
                raise RuntimeError(
                    f"refusing to overwrite modified OLAR source {destination}: {existing_sha}",
                )
        else:
            destination.write_bytes(data)
        written.append(destination)
    return tuple(written)


def main() -> int:
    candidate_dir = Path(__file__).resolve().parent
    for path in reconstruct(candidate_dir):
        print(path.relative_to(candidate_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
