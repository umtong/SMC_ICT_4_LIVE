#!/usr/bin/env python3
"""Artifact-layout adapter for the unchanged V44 staged research.

Historical V31 artifacts contain the trusted rich stream under
``original/source/source/rich`` while newer artifacts may use
``original/source/rich``. This adapter discovers the manifest rather than
encoding another version-specific path, exposes it at the orchestrator's
standard location, and delegates all research logic unchanged.
"""
from __future__ import annotations

from pathlib import Path

import run_v44_research as base


def discover_source(sources: Path, week: base.Week) -> Path:
    matches = sorted(
        path
        for path in sources.glob(f"candidate-04-v31-ablation-{week.name}-*")
        if path.is_dir()
    )
    if not matches:
        raise FileNotFoundError(f"missing V31 source for {week.name}")
    source = matches[0]
    signals = source / "ablated/signals/signals.json"
    if not signals.is_file():
        raise FileNotFoundError(f"missing frozen signals: {signals}")

    standard = source / "original/source/rich"
    if not (standard / "data_manifest.json").is_file():
        candidates = sorted(
            source.glob("original/**/rich/data_manifest.json"),
            key=lambda path: (len(path.parts), str(path)),
        )
        if not candidates:
            raise FileNotFoundError(
                f"missing trusted rich-data manifest under {source / 'original'}"
            )
        actual = candidates[0].parent.resolve()
        standard.parent.mkdir(parents=True, exist_ok=True)
        if standard.is_symlink() or standard.exists():
            if standard.is_symlink():
                standard.unlink()
            elif standard.is_dir() and not any(standard.iterdir()):
                standard.rmdir()
            else:
                raise RuntimeError(
                    f"nonstandard nonempty rich path blocks adapter: {standard}"
                )
        standard.symlink_to(actual, target_is_directory=True)
    return source


base.find_source = discover_source

if __name__ == "__main__":
    base.main()
