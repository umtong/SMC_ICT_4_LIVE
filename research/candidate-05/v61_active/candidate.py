#!/usr/bin/env python3
"""Process-isolated launcher for the v61 evidence-selected composite."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
if str(PARENT) not in sys.path:
    sys.path.append(str(PARENT))

components = {
    item.strip()
    for item in os.environ.get("CANDIDATE05_COMPONENTS", "").split(",")
    if item.strip()
}
if components & {"v55", "v58", "v59"}:
    from spot_price_discovery_contract import install as install_spot_price_discovery  # noqa: E402

    install_spot_price_discovery()

SPEC = importlib.util.spec_from_file_location(
    "_candidate05_v61_pipeline_base",
    PARENT / "candidate.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load parent candidate pipeline")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)


def run_backtest_isolated_v61(
    *,
    config_path: Path,
    build_start: Any,
    build_end: Any,
    evaluation_start: Any,
    evaluation_end: Any,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "stage",
        "--config",
        str(config_path.resolve()),
        "--build-start",
        str(build_start),
        "--build-end",
        str(build_end),
        "--evaluation-start",
        str(evaluation_start),
        "--evaluation-end",
        str(evaluation_end),
        "--cache",
        str(cache.resolve()),
        "--output",
        str(output.resolve()),
    ]
    subprocess.run(command, check=True)
    metrics_path = output.resolve() / "metrics.json"
    if not metrics_path.exists():
        raise RuntimeError(
            f"isolated v61 Nautilus stage did not produce {metrics_path}",
        )
    return BASE.json.loads(metrics_path.read_text(encoding="utf-8"))


BASE.run_backtest_isolated = run_backtest_isolated_v61

if __name__ == "__main__":
    BASE.main()
