#!/usr/bin/env python3
"""Prevent downloading the day beginning at evaluation_end_exclusive."""
from __future__ import annotations

from pathlib import Path

MARKER = "C11_MICRO_END_EXCLUSIVE_DOWNLOAD"


def apply(root: Path) -> int:
    path = root / "run_microstructure_nautilus.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return 0
    old = '''    frame, files = load_one_second_bars(warmup_start, evaluation_end, data_dir)
'''
    new = '''    # C11_MICRO_END_EXCLUSIVE_DOWNLOAD: evaluation_end is a date at
    # 00:00 and is not part of the sample.  Do not open its daily archive.
    frame, files = load_one_second_bars(
        warmup_start,
        evaluation_end - timedelta(days=1),
        data_dir,
    )
'''
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"microstructure end-exclusive anchor count={count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"microstructure evaluation-window fix applied: {apply(root)}")


if __name__ == "__main__":
    main()
