#!/usr/bin/env python3
"""Patch V27's monthly kline reader without changing model or market logic."""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''    numeric_time = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    raw = raw.loc[numeric_time.notna()].copy()
    raw.iloc[:, 0] = numeric_time.loc[numeric_time.notna()].astype("int64")
    for column in (1, 2, 3, 4, 5, 7, 9, 10):
        raw.iloc[:, column] = pd.to_numeric(raw.iloc[:, column], errors="raise")
    timestamps = raw.iloc[:, 0].astype("int64").to_numpy()
'''
NEW = '''    numeric_time = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    valid = numeric_time.notna()
    raw = raw.loc[valid].copy()
    timestamps = numeric_time.loc[valid].to_numpy(dtype=np.int64)
    for column in (1, 2, 3, 4, 5, 7, 9, 10):
        raw.iloc[:, column] = pd.to_numeric(raw.iloc[:, column], errors="raise")
'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW in source:
        return False
    if OLD not in source:
        raise RuntimeError("V27 parser block not found")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path("research/candidate-03/derive_nt_lvcfr_v27_signals.py"),
    )
    args = parser.parse_args()
    print(f"V27 parser patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
