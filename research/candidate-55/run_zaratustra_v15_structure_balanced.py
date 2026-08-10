"""Untouched short-window comparison for the balanced V15 state plateau."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
_BASE_PATH = CANDIDATE / "run_zaratustra_v15_structure.py"
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v15_structure_balanced_runner_base",
    _BASE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load structural runner: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

_BASE.WORK = ROOT / ".work" / "candidate-55-v15-structure-balanced"
_BASE.CACHE = ROOT / ".cache" / "candidate-55-v15-structure-balanced"
_BASE.ARTIFACTS = ROOT / "artifacts" / "candidate-55" / "v15-structure-balanced"


def _rewrite_manifest() -> None:
    path = _BASE.WORK / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "profile": "BALANCED_PLATEAU",
            "comparison_role": (
                "Neighbouring state plateau tested on the same untouched "
                "short selection windows; no medium or long result is spent."
            ),
            "replaced": {
                "DI_state": (
                    "15m return >= -0.2% inside a negative 4h auction"
                ),
                "BB_state": (
                    "15m downside impulse >=1.2 current 5m ATR with at "
                    "least 3/4 peers negative over 60m"
                ),
            },
        }
    )
    _BASE.dump(path, manifest)


def main() -> int:
    config = _BASE.create_config()
    _rewrite_manifest()
    rows: list[dict[str, Any]] = []
    for stage, interval in _BASE.SHORT_WINDOWS.items():
        profile_stage = f"balanced-{stage}"
        code = _BASE.run_backtest(config, profile_stage, interval)
        rows.append(_BASE.read_result(profile_stage, code, 7))
    worth_mid, analysis = _BASE.short_evidence_worth_mid(rows)
    result = {
        "candidate": "candidate-55",
        "family": "V15_STRUCTURAL_SHORT_REPAIR",
        "profile": "BALANCED_PLATEAU",
        "short_windows": rows,
        "short_analysis": analysis,
        "medium_or_long_run": False,
        "medium_resource_would_be_justified": worth_mid,
        "interpretation": (
            "Compare opportunity density, gross-profit preservation, gross-"
            "loss concentration and family contribution against the core "
            "profile. Net sign alone does not select the repair."
        ),
    }
    _BASE.dump(
        _BASE.ARTIFACTS / "balanced-short-final-result.json",
        result,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
