#!/usr/bin/env python3
"""Idempotent fail-closed migration for Candidate 11's GTD order contract."""
from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> bool:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return False
    if source.count(old) != 1:
        raise SystemExit(f"{label}: expected one migration anchor, found {source.count(old)}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def main() -> None:
    root = Path(__file__).resolve().parent
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

    changed = int(
        replace_once(
            run_path,
            "                    expire_time=plan.expire_ts_ns,",
            "                    expire_time=datetime.fromtimestamp(plan.expire_ts_ns / 1_000_000_000, tz=timezone.utc),",
            "GTD expiration datetime",
        ),
    )

    source = test_path.read_text(encoding="utf-8")
    test_name = "test_gtd_expiry_uses_timezone_aware_datetime"
    if test_name not in source:
        anchor = '''        self.assertNotIn("def backtest_loop", source)\n'''
        addition = '''        self.assertNotIn("def backtest_loop", source)\n\n    def test_gtd_expiry_uses_timezone_aware_datetime(self) -> None:\n        source = (ROOT / "run.py").read_text(encoding="utf-8")\n        self.assertIn("expire_time=datetime.fromtimestamp(", source)\n        self.assertIn("tz=timezone.utc", source)\n        self.assertNotIn("expire_time=plan.expire_ts_ns", source)\n'''
        if source.count(anchor) != 1:
            raise SystemExit("GTD contract-test anchor is not unique")
        test_path.write_text(source.replace(anchor, addition, 1), encoding="utf-8")
        changed += 1

    print(f"SCDAM GTD migrations applied: {changed}")


if __name__ == "__main__":
    main()
