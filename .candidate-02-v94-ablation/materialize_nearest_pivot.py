#!/usr/bin/env python3
"""Materialize the exact one-variable v94 target-selection ablation."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import difflib
import json

SOURCE = Path("research/candidate-02/v94_multilevel_common_breakout_core.py")
OUTPUT = Path("/tmp/v94_nearest_pivot_core.py")
MANIFEST = Path("artifacts-v94-nearest-pivot-source-manifest.json")
NEEDLE = "        if math.isfinite(rr) and minimum_rr <= rr <= maximum_rr:\n"
REPLACEMENT = "        if math.isfinite(rr) and rr <= maximum_rr:\n"


def main() -> None:
    original = SOURCE.read_text(encoding="utf-8")
    if original.count(NEEDLE) != 1:
        raise RuntimeError(f"expected one target minimum-RR predicate, found {original.count(NEEDLE)}")
    generated = original.replace(NEEDLE, REPLACEMENT)
    diff = list(
        difflib.unified_diff(
            original.splitlines(),
            generated.splitlines(),
            fromfile=str(SOURCE),
            tofile=str(OUTPUT),
            lineterm="",
        )
    )
    changed = [line for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    expected = [
        "-        if math.isfinite(rr) and minimum_rr <= rr <= maximum_rr:",
        "+        if math.isfinite(rr) and rr <= maximum_rr:",
    ]
    if changed != expected:
        raise RuntimeError(f"unexpected source delta: {changed}")
    OUTPUT.write_text(generated, encoding="utf-8")
    manifest = {
        "status": "EXACT_SINGLE_TARGET_FILTER_REMOVED",
        "source_path": str(SOURCE),
        "generated_path": str(OUTPUT),
        "source_sha256": sha256(original.encode()).hexdigest(),
        "generated_sha256": sha256(generated.encode()).hexdigest(),
        "removed_predicate": "minimum_rr <= rr",
        "source_diff": diff,
        "changed_lines": changed,
        "performance_engine": "NautilusTrader 1.230.0",
        "custom_backtest_engine": False,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
