#!/usr/bin/env python3
"""Reuse the proven candidate-4 patcher while varying only route completion distance.

The v2 patcher is the source of truth for locating and modifying the inherited
candidate-4 engine.  This wrapper substitutes its fixed 0.50 route fraction in a
temporary copy, then executes that copy against an independently checked-out
candidate-4 runtime.  No detector, stop, entry, or account rule is changed.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--fraction", required=True, type=float)
    args = parser.parse_args()

    fraction = float(args.fraction)
    if not 0.08 <= fraction <= 0.80:
        raise SystemExit(f"route fraction outside research range: {fraction}")

    here = Path(__file__).resolve()
    base = here.parents[1] / "candidate-ml-first-liquidity-response-v2" / "patch_exact_half_route.py"
    source = base.read_text(encoding="utf-8")

    # The inherited patcher was deliberately written with one fixed half-route
    # constant.  Replace numeric tokens only; ordinary text containing 0.50 is
    # harmless but is also kept consistent in the generated patch message.
    replacement = format(fraction, ".12g")
    updated, count = re.subn(r"(?<![0-9.])0\.50(?![0-9.])", replacement, source)
    if count == 0:
        updated, count = re.subn(r"(?<![0-9.])0\.5(?![0-9.])", replacement, source)
    if count == 0:
        raise SystemExit("could not locate the fixed half-route constant in the v2 patcher")

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix="_patch_route_fraction.py",
        dir=base.parent,
        delete=False,
    ) as handle:
        handle.write(updated)
        temporary = Path(handle.name)

    try:
        subprocess.run(
            [sys.executable, str(temporary), "--root", str(args.root)],
            check=True,
        )
    finally:
        temporary.unlink(missing_ok=True)

    print(f"materialized candidate-4 route target fraction {fraction:.6f}")


if __name__ == "__main__":
    main()
