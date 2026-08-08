#!/usr/bin/env python3
"""Apply the single frozen timestamp-contract repair to Candidate 16 v4."""
from __future__ import annotations
from pathlib import Path
import sys

EXPECTED = 'frame["minute_start_ns"] = timestamp.astype("int64")'
REPLACEMENT = 'frame["minute_start_ns"] = timestamp.astype("datetime64[ns, UTC]").astype("int64")'


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_features_v4.py PATH_TO_FEATURES_V4")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    if REPLACEMENT in text:
        print("timestamp contract already repaired")
        return
    if EXPECTED not in text:
        raise SystemExit("frozen Candidate 16 source drifted; expected line not found")
    path.write_text(text.replace(EXPECTED, REPLACEMENT), encoding="utf-8")
    print(f"patched {path}")


if __name__ == "__main__":
    main()
