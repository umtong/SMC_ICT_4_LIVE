#!/usr/bin/env python3
"""Process-isolated Candidate 05 v56 launcher with causal spot observations."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
if str(PARENT) not in sys.path:
    sys.path.append(str(PARENT))

from timestamp_contract import install as install_timestamp_contract  # noqa: E402
from wrangler_contract import install as install_wrangler_contract  # noqa: E402
from positioning_contract import install as install_positioning_contract  # noqa: E402
from basis_contract import install as install_basis_contract  # noqa: E402
from spot_price_discovery_contract import (  # noqa: E402
    install as install_spot_price_discovery_contract,
)

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()
install_spot_price_discovery_contract()

SPEC = importlib.util.spec_from_file_location(
    "_candidate05_v56_pipeline_base",
    PARENT / "candidate.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load parent candidate pipeline")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)


def run_backtest_isolated_v56(
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
            f"isolated v56 Nautilus stage did not produce {metrics_path}",
        )
    return BASE.json.loads(metrics_path.read_text(encoding="utf-8"))


BASE.run_backtest_isolated = run_backtest_isolated_v56

if __name__ == "__main__":
    BASE.main()
