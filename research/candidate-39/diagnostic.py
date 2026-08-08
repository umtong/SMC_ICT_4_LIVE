#!/usr/bin/env python3
"""Structural, no-PnL diagnostic for Candidate 39.

The feature observation is frozen at the first response minute.  The following
two completed bars may confirm or reject that interaction, so the diagnostic
matches the live Nautilus strategy's causal separation.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "candidate-35"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import router as _router
sys.modules["router"] = _router
_spec = importlib.util.spec_from_file_location(
    "_candidate39_reused_candidate35_diagnostic",
    BASE / "diagnostic.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load diagnostic shell from {BASE / 'diagnostic.py'}")
_base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base
_spec.loader.exec_module(_base)
_original_feature_at = _base._feature_at


def _interaction_feature_at(
    frame: Any,
    index: int,
    *,
    bar_ts: int,
    max_age_seconds: float,
) -> Any:
    del bar_ts
    interaction_index = index - 2
    if interaction_index < 0:
        return _router.FeatureObservation(0, ready=False)
    interaction_ts = int(frame.iloc[interaction_index]["observed_time_ns"])
    return _original_feature_at(
        frame,
        interaction_index,
        bar_ts=interaction_ts,
        max_age_seconds=max_age_seconds,
    )


_base._feature_at = _interaction_feature_at


def diagnose(*, input_root: Path, config_path: Path, output: Path) -> dict[str, Any]:
    result = _base.diagnose(
        input_root=input_root,
        config_path=config_path,
        output=output,
    )
    result["schema"] = "candidate-39-short-router-diagnostic-v1"
    result["candidate"] = "candidate-39-causal-auction-state-router"
    result["non_scalping"] = True
    result["feature_observation"] = "first response minute, frozen before later initiative"
    result["claim_scope"] = "STRUCTURAL_DIAGNOSTIC_ONLY_NO_PNL_NO_NAV_CLAIM"
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(diagnose(
        input_root=args.input_root,
        config_path=args.config,
        output=args.output,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
