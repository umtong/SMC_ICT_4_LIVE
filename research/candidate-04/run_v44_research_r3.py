#!/usr/bin/env python3
"""Artifact-layout and build-range adapter for unchanged V44 research.

Historical V31 artifacts contain the trusted rich stream under
``original/source/source/rich`` while newer artifacts may use
``original/source/rich``. This adapter discovers the manifest rather than
encoding another version-specific path, exposes it at the orchestrator's
standard location, and forwards the exact weekly build/evaluation boundaries to
the rich-data loader and NautilusTrader.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

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


_original_run_week = base.run_week


def run_week_with_exact_range(
    week: base.Week,
    *,
    sources: Path,
    output_root: Path,
    cache_root: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    weekly_env = dict(env)
    weekly_env.update(
        {
            "C04_BUILD_START": week.build_start,
            "C04_BUILD_END": week.build_end,
            "C04_EVALUATION_START": week.evaluation_start,
            "C04_EVALUATION_END": week.evaluation_end,
        }
    )
    return _original_run_week(
        week,
        sources=sources,
        output_root=output_root,
        cache_root=cache_root,
        env=weekly_env,
    )


base.find_source = discover_source
base.run_week = run_week_with_exact_range

if __name__ == "__main__":
    base.main()
