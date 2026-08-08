#!/usr/bin/env python3
"""Fail-closed materialization and idempotent migrations for Candidate 11."""
from __future__ import annotations

from base64 import b64decode
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
import tarfile

from apply_independent_aac_draw import apply as apply_independent_aac_draw


COMPLEX_ARCHIVE = "complex_runtime.tar.xz"
COMPLEX_PARTS = tuple(f"complex_runtime.part{i:02d}.b64" for i in range(6))
COMPLEX_ARCHIVE_SHA256 = "c43e99ed573797c64783c1791480e465ad8e8d8b51b7c4a1c945a0ee1a07076e"
COMPLEX_FILE_SHA256 = {
    "complex_engine.py": "9837624b72c1b1ef4a37e819d957365a2124c90f8e3ae849c1b5868971ce28f5",
    "run_complex_nautilus.py": "a64a0a36cb7fdeb26b3b174e3e1d3233b40380ea62ca9abb65094530234833c1",
}


def replace_once(path: Path, old: str, new: str, label: str) -> bool:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return False
    if source.count(old) != 1:
        raise SystemExit(f"{label}: expected one migration anchor, found {source.count(old)}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def _safe_member(name: str) -> bool:
    pure = PurePosixPath(name)
    return not pure.is_absolute() and ".." not in pure.parts and len(pure.parts) == 1


def materialize_complex(root: Path) -> int:
    """Materialize the exact locally contract-tested synchronized market source."""
    part_paths = tuple(root / name for name in COMPLEX_PARTS)
    present = tuple(path.name for path in part_paths if path.exists())
    if not present:
        missing = [name for name in COMPLEX_FILE_SHA256 if not (root / name).is_file()]
        if missing:
            raise SystemExit(f"synchronized complex source is incomplete: {missing}")
        return 0
    if present != COMPLEX_PARTS:
        missing_parts = sorted(set(COMPLEX_PARTS) - set(present))
        raise SystemExit(f"incomplete synchronized source part set: {missing_parts}")

    encoded = "".join(path.read_text(encoding="ascii") for path in part_paths)
    try:
        payload = b64decode(encoded, validate=True)
    except Exception as exc:
        raise SystemExit(f"invalid synchronized source base64: {exc}") from exc
    actual_archive_hash = sha256(payload).hexdigest()
    if actual_archive_hash != COMPLEX_ARCHIVE_SHA256:
        raise SystemExit(
            "synchronized source archive SHA-256 mismatch: "
            f"expected={COMPLEX_ARCHIVE_SHA256} actual={actual_archive_hash}",
        )

    try:
        with tarfile.open(fileobj=BytesIO(payload), mode="r:xz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise SystemExit("duplicate synchronized source member")
            if set(names) != set(COMPLEX_FILE_SHA256):
                raise SystemExit(
                    "synchronized source member set mismatch: "
                    f"expected={sorted(COMPLEX_FILE_SHA256)} actual={sorted(names)}",
                )
            for member in members:
                if not member.isfile() or not _safe_member(member.name):
                    raise SystemExit(f"unsafe synchronized source member: {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise SystemExit(f"unreadable synchronized source member: {member.name}")
                data = source.read()
                actual = sha256(data).hexdigest()
                expected = COMPLEX_FILE_SHA256[member.name]
                if actual != expected:
                    raise SystemExit(
                        f"synchronized source hash mismatch for {member.name}: "
                        f"expected={expected} actual={actual}",
                    )
                temporary = root / f".{member.name}.tmp"
                temporary.write_bytes(data)
                temporary.replace(root / member.name)
    except tarfile.TarError as exc:
        raise SystemExit(f"invalid synchronized source archive: {exc}") from exc

    for path in part_paths:
        path.unlink()
    (root / COMPLEX_ARCHIVE).unlink(missing_ok=True)
    print("materialized synchronized four-market FAR/AAC source")
    return len(COMPLEX_FILE_SHA256)


def main() -> None:
    root = Path(__file__).resolve().parent
    changed = materialize_complex(root)

    run_path = root / "run.py"
    test_path = root / "test_logic.py"
    logic_path = root / "logic.py"
    missing = [path.name for path in (run_path, test_path, logic_path) if not path.is_file()]
    if missing:
        raise SystemExit(f"materialized SCDAM source is incomplete: {missing}")

    logic_source = logic_path.read_text(encoding="utf-8")
    for marker in (
        '"OBSERVE", "FAR_CONFIRMED"',
        '"OBSERVE", "AAC_CONFIRMED"',
        "previous_state = self.active.state",
    ):
        if marker not in logic_source:
            raise SystemExit(f"required causal-ledger migration missing: {marker}")

    changed += int(
        replace_once(
            run_path,
            "                    expire_time=datetime.fromtimestamp(plan.expire_ts_ns / 1_000_000_000, tz=timezone.utc),",
            "                    expire_time=datetime.fromtimestamp(plan.expire_ts_ns / 1_000_000_000, tz=timezone.utc) + timedelta(microseconds=1),",
            "inclusive GTD final-bar ordering",
        ),
    )

    source = test_path.read_text(encoding="utf-8")
    old_test = '''    def test_gtd_expiry_uses_timezone_aware_datetime(self) -> None:\n        source = (ROOT / "run.py").read_text(encoding="utf-8")\n        self.assertIn("expire_time=datetime.fromtimestamp(", source)\n        self.assertIn("tz=timezone.utc", source)\n        self.assertNotIn("expire_time=plan.expire_ts_ns", source)\n'''
    new_test = '''    def test_gtd_expiry_uses_timezone_aware_datetime(self) -> None:\n        source = (ROOT / "run.py").read_text(encoding="utf-8")\n        self.assertIn("expire_time=datetime.fromtimestamp(", source)\n        self.assertIn("tz=timezone.utc", source)\n        self.assertIn("+ timedelta(microseconds=1)", source)\n        self.assertNotIn("expire_time=plan.expire_ts_ns", source)\n'''
    changed += int(replace_once(test_path, old_test, new_test, "inclusive GTD contract test"))
    changed += apply_independent_aac_draw(root)

    print(f"Candidate 11 materialization/migrations applied: {changed}")


if __name__ == "__main__":
    main()
