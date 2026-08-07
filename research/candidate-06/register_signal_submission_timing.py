#!/usr/bin/env python3
"""Register causal signal-close submission for AIMD without changing legacy runs."""

from __future__ import annotations

from pathlib import Path

STRATEGY_ANCHOR = '''            if step.signal is not None:\n                self._pending_signal = step.signal\n                self._pending_created_index = snapshot.index\n                self.diagnostics["signals_armed"] += 1\n'''
STRATEGY_PATCH = '''            if step.signal is not None:\n                self.diagnostics["signals_armed"] += 1\n                timing = str(\n                    self._logic_params.get(\n                        "signal_submission_timing",\n                        "NEXT_COMPLETED_BAR",\n                    ),\n                ).upper()\n                timing_counts = self.diagnostics.setdefault(\n                    "signal_submission_timing_counts",\n                    {},\n                )\n                timing_counts[timing] = int(timing_counts.get(timing, 0)) + 1\n                if timing == "ON_SIGNAL_CLOSE":\n                    # The scenario signal is created only after this completed\n                    # bar is observable. Nautilus processes a market order\n                    # submitted from on_bar against the current bar close; it\n                    # cannot use the completed bar's high/low path retroactively.\n                    self._attempt_entry(step.signal, snapshot)\n                elif timing == "NEXT_COMPLETED_BAR":\n                    self._pending_signal = step.signal\n                    self._pending_created_index = snapshot.index\n                else:\n                    raise ValueError(\n                        f"unsupported signal_submission_timing: {timing}",\n                    )\n'''

MATRIX_ANCHOR = '''            "engine": "AUCTION_IMBALANCE_MIGRATION_DISCOVERY",\n'''
MATRIX_PATCH = MATRIX_ANCHOR + '''            "signal_submission_timing": "ON_SIGNAL_CLOSE",\n'''


def _replace_once(text: str, anchor: str, replacement: str, label: str) -> str:
    if replacement in text:
        return text
    if text.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor count changed: {text.count(anchor)}")
    return text.replace(anchor, replacement, 1)


def main() -> int:
    root = Path(__file__).resolve().parent
    strategy = root / "nautilus_strategy.py"
    matrix = root / "run_auction_migration_matrix.py"
    strategy_text = _replace_once(
        strategy.read_text(encoding="utf-8"),
        STRATEGY_ANCHOR,
        STRATEGY_PATCH,
        "strategy signal submission",
    )
    matrix_text = _replace_once(
        matrix.read_text(encoding="utf-8"),
        MATRIX_ANCHOR,
        MATRIX_PATCH,
        "AIMD matrix timing",
    )
    strategy.write_text(strategy_text, encoding="utf-8")
    matrix.write_text(matrix_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
