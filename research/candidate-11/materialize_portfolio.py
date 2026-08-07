#!/usr/bin/env python3
"""Fail-closed materializer and API migration for the four-market SCDAM runner."""
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


def _replace_once(path: Path, old: str, new: str, label: str) -> int:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return 0
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one migration anchor, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def _migrate_runner(root: Path) -> int:
    """Keep the frozen portfolio logic aligned with the current causal-engine API.

    These replacements change no detector threshold, session definition, cost
    assumption, risk rate, or arbitration rule. They only route the portfolio
    adapter through the same public methods already used by the single-market
    Nautilus runner.
    """
    path = root / "run_portfolio_scdam.py"
    changed = 0
    changed += _replace_once(
        path,
        "                plan = self.logic[symbol].update(observation)",
        "                plan = self.logic[symbol].on_bar(observation)",
        "portfolio detector callback",
    )
    changed += _replace_once(
        path,
        "                    self.logic[self.active_symbol].mark_entry_filled(scenario_id, ts_ns)",
        "                    self.logic[self.active_symbol].mark_entry_filled(\n"
        "                        ts_ns,\n"
        "                        {\"scenario_id\": scenario_id, \"symbol\": self.active_symbol},\n"
        "                    )",
        "portfolio parent-fill callback",
    )
    changed += _replace_once(
        path,
        "            self.logic[symbol].mark_submitted(plan, self.last_ts_ns)",
        "            self.logic[symbol].mark_submitted(\n"
        "                plan,\n"
        "                decision.quantity,\n"
        "                {\"symbol\": symbol, \"scenario_id\": plan.scenario_id},\n"
        "            )",
        "portfolio submission callback",
    )
    return changed


def main() -> None:
    root = Path(__file__).resolve().parent
    part_paths = tuple(root / name for name in PARTS)
    present = tuple(path.name for path in part_paths if path.exists())
    if not present:
        missing = [name for name in FILES if not (root / name).is_file()]
        if missing:
            raise SystemExit(f"portfolio source parts and materialized files are missing: {missing}")
        changed = _migrate_runner(root)
        print(f"portfolio SCDAM source already materialized; API migrations applied: {changed}")
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
    changed = _migrate_runner(root)
    print(f"materialized {len(FILES)} verified portfolio SCDAM files; API migrations applied: {changed}")


if __name__ == "__main__":
    main()
