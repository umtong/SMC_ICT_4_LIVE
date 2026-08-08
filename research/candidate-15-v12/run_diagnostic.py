#!/usr/bin/env python3
"""Execute the V12 diagnostic with Candidate 15's audited timestamp loader."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

import diagnose_cross_predictive_spillover as diagnostic

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CANDIDATE14 = REPO / "research" / "candidate-14"
CANDIDATE15 = REPO / "research" / "candidate-15"


def _load_candidate15_v11_runner() -> Any:
    """Reuse the V11 materialized loader, including strict numeric open_time."""
    path = CANDIDATE15 / "run_leadership_scdam_v11.py"
    spec = importlib.util.spec_from_file_location("candidate15_v11_for_v12", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    inserted: list[str] = []
    for item in (str(CANDIDATE15), str(CANDIDATE14)):
        if item not in sys.path:
            sys.path.insert(len(inserted), item)
            inserted.append(item)
    try:
        spec.loader.exec_module(module)
    finally:
        for item in inserted:
            try:
                sys.path.remove(item)
            except ValueError:
                pass
    return module


# The diagnostic's original loader deliberately imported Candidate 14 directly.
# Replace only that engineering boundary; event logic and evaluation remain frozen.
diagnostic._load_candidate14_runner = _load_candidate15_v11_runner


if __name__ == "__main__":
    raise SystemExit(diagnostic.main())
