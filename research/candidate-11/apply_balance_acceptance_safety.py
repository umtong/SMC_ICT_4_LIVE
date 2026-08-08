#!/usr/bin/env python3
"""Apply execution-validity guards to balance-acceptance plans.

This does not relax or optimize the alpha conditions.  It prevents a plan from
being emitted after the frozen measured-move target has already traded during
the confirmation bar, because such a target is no longer available to a later
passive entry.
"""
from __future__ import annotations

from pathlib import Path

MARKER = "BALANCE_TARGET_CONSUMED_BEFORE_ENTRY"


def main() -> None:
    path = Path(__file__).resolve().parent / "microstructure_v3.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print("balance-acceptance target-consumption guard already applied")
        return

    old = '''        target = (
            event.balance.high + event.balance.range
            if direction == "LONG"
            else event.balance.low - event.balance.range
        )
        valid = stop < entry < target if direction == "LONG" else target < entry < stop
'''
    new = '''        target = (
            event.balance.high + event.balance.range
            if direction == "LONG"
            else event.balance.low - event.balance.range
        )
        target_consumed = (
            bar.high >= target if direction == "LONG" else bar.low <= target
        )
        if target_consumed:
            self.skips["BALANCE_TARGET_CONSUMED_BEFORE_ENTRY"] += 1
            self._record(
                "BALANCE_ACCEPTANCE_TERMINATED",
                bar.ts_ns,
                reason="BALANCE_TARGET_CONSUMED_BEFORE_ENTRY",
                direction=direction,
                target=target,
            )
            return None
        valid = stop < entry < target if direction == "LONG" else target < entry < stop
'''
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"balance target guard anchor count={count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    print("applied balance-acceptance target-consumption guard")


if __name__ == "__main__":
    main()
