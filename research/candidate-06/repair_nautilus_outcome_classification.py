#!/usr/bin/env python3
"""Idempotently wire tick-aware native close classification into lifecycle."""

from __future__ import annotations

from pathlib import Path


PATH = Path(__file__).resolve().parent / "nautilus_lifecycle.py"
IMPORT_ANCHOR = (
    "from excursion_diagnostics import calculate_excursion_diagnostics\n"
)
IMPORT_LINE = (
    "from nautilus_outcome_classification import "
    "classify_position_outcome\n"
)
OLD_BLOCK = '''        forced = trade.get("forced_exit_reason")
        if forced:
            outcome = str(forced)
        elif trade["direction"] == "LONG":
            if close_price >= float(trade["target_price"]) - tick:
                outcome = "TARGET"
            elif close_price <= float(trade["stop_price"]) + tick:
                outcome = "STOP"
            else:
                outcome = "OTHER_EXIT"
        else:
            if close_price <= float(trade["target_price"]) + tick:
                outcome = "TARGET"
            elif close_price >= float(trade["stop_price"]) - tick:
                outcome = "STOP"
            else:
                outcome = "OTHER_EXIT"
'''
NEW_BLOCK = '''        outcome = classify_position_outcome(
            direction=str(trade["direction"]),
            close_price=close_price,
            target_price=float(trade["target_price"]),
            stop_price=float(trade["stop_price"]),
            tick=tick,
            forced_exit_reason=trade.get("forced_exit_reason"),
        )
'''


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    changed = False
    if IMPORT_LINE not in text:
        count = text.count(IMPORT_ANCHOR)
        if count != 1:
            raise RuntimeError(
                f"expected one lifecycle import anchor, found {count}",
            )
        text = text.replace(
            IMPORT_ANCHOR,
            IMPORT_ANCHOR + IMPORT_LINE,
            1,
        )
        changed = True
    if NEW_BLOCK not in text:
        count = text.count(OLD_BLOCK)
        if count != 1:
            raise RuntimeError(
                f"expected one legacy outcome block, found {count}",
            )
        text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)
        changed = True
    if changed:
        PATH.write_text(text, encoding="utf-8")
        print("patched nautilus_lifecycle.py")
    else:
        print("nautilus_lifecycle.py already patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
