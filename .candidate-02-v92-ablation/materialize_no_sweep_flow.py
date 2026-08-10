#!/usr/bin/env python3
"""Materialize the exact one-variable v92 ablation source.

The script performs one strict source transformation: it removes the predicate
which rejects a sweep when aggressive flow is not aligned with the swept
boundary.  It changes no other source text and writes a cryptographic manifest.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import difflib
import json

SOURCE = Path("research/candidate-02/v92_session_liquidity_sweep_core.py")
OUTPUT = Path("/tmp/v92_no_sweep_flow_core.py")
MANIFEST = Path("artifacts-v92-no-sweep-flow-source-manifest.json")
NEEDLE = "            if flow_alignment < flow_floor:\n                continue\n"
REPLACEMENT = (
    "            # Locked single ablation: observe and record sweep flow, but\n"
    "            # do not require it to align with the swept boundary.\n"
)


def main() -> None:
    original = SOURCE.read_text(encoding="utf-8")
    occurrences = original.count(NEEDLE)
    if occurrences != 1:
        raise RuntimeError(f"expected one sweep-flow predicate, found {occurrences}")
    generated = original.replace(NEEDLE, REPLACEMENT)
    if generated.count(REPLACEMENT) != 1:
        raise RuntimeError("ablation replacement was not unique")
    OUTPUT.write_text(generated, encoding="utf-8")
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
    expected_changed = [
        "-            if flow_alignment < flow_floor:",
        "-                continue",
        "+            # Locked single ablation: observe and record sweep flow, but",
        "+            # do not require it to align with the swept boundary.",
    ]
    if changed != expected_changed:
        raise RuntimeError(f"unexpected source delta: {changed}")
    manifest = {
        "status": "EXACT_SINGLE_SOURCE_PREDICATE_REMOVED",
        "source_path": str(SOURCE),
        "generated_path": str(OUTPUT),
        "source_sha256": sha256(original.encode()).hexdigest(),
        "generated_sha256": sha256(generated.encode()).hexdigest(),
        "removed_predicate": "flow_alignment < flow_floor",
        "source_diff": diff,
        "changed_lines": changed,
        "custom_backtest_engine": False,
        "performance_engine": "NautilusTrader 1.230.0",
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
