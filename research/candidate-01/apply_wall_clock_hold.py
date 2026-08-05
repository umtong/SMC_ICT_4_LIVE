#!/usr/bin/env python3
"""Add an optional causal wall-clock hold contract to the shared simulator.

The default bar-count behavior remains byte-for-byte equivalent for every
existing caller.  Event clocks can opt into an absolute nanosecond horizon so
changing market activity cannot silently turn a four-hour day trade into a
much shorter or longer trade.
"""

from __future__ import annotations

from pathlib import Path


HERE = Path(__file__).resolve().parent
PATH = HERE / "impact_regime_probe.py"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    old_signature = """    cost: float,\n    exit_on_boundary_reacceptance: bool = False,\n) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, pd.DataFrame]:\n"""
    new_signature = """    cost: float,\n    exit_on_boundary_reacceptance: bool = False,\n    maximum_hold_ns: int | None = None,\n) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, pd.DataFrame]:\n"""
    if old_signature in text:
        text = text.replace(old_signature, new_signature, 1)
    elif new_signature not in text:
        raise RuntimeError("shared simulator signature no longer matches expected source")

    old_condition = """            elif active.bars_held >= MAX_HOLD_BARS:\n                closed = close_position(\n"""
    new_condition = """            elif (\n                maximum_hold_ns is not None\n                and bar.end_time_ns - active.entry_time_ns >= maximum_hold_ns\n            ) or (\n                maximum_hold_ns is None\n                and active.bars_held >= MAX_HOLD_BARS\n            ):\n                closed = close_position(\n"""
    if old_condition in text:
        text = text.replace(old_condition, new_condition, 1)
    elif new_condition not in text:
        raise RuntimeError("shared simulator hold condition no longer matches expected source")

    PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
