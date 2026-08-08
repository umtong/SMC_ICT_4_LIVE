"""Evidence-selected active shim for Candidate 05 v60.

``CANDIDATE05_COMPONENTS`` must contain a sorted comma-separated subset of
``v55,v56,v58``. The workflow derives it only from frozen OOS PASS decisions.
Optional v55 threshold environment values are applied before importing the v55
module and are copied from its promoted diagnostic-loop decision.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


_PARENT = Path(__file__).resolve().parents[1]
if str(_PARENT) not in sys.path:
    sys.path.append(str(_PARENT))

import spot_led_repricing_logic as _spot_logic  # noqa: E402

if "V55_SPOT_LEAD_BPS_MIN" in os.environ:
    _spot_logic.SPOT_LEAD_BPS_MIN = float(os.environ["V55_SPOT_LEAD_BPS_MIN"])
if "V55_ACCEPTANCE_FLOW_MIN" in os.environ:
    _spot_logic.SPOT_CONTEXT_ACCEPTANCE_FLOW_3M_MIN = float(
        os.environ["V55_ACCEPTANCE_FLOW_MIN"],
    )
if "V55_CONTEXT_MIN_AGE_BARS" in os.environ:
    _spot_logic.SPOT_CONTEXT_MIN_AGE_BARS = int(
        os.environ["V55_CONTEXT_MIN_AGE_BARS"],
    )

_BASE_PATH = _PARENT / "strategy.py"
_SPEC = importlib.util.spec_from_file_location("_candidate05_v60_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load base strategy from {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

_WRAPPER = sys.modules[__name__]
sys.modules["strategy"] = _BASE
try:
    from strategy_v55_spot_price_discovery import SpotLedPriceDiscoveryStrategy  # noqa: E402
    from strategy_v56_early_flow_retrace import EarlyFlowFirstRetraceStrategy  # noqa: E402
    from strategy_v58_forced_basis_reversion import ForcedBasisReversionStrategy  # noqa: E402
    from strategy_v60_promoted_composite import (  # noqa: E402
        EarlyFlowAndBasisComposite,
        SpotAndBasisComposite,
        SpotAndEarlyFlowComposite,
        SpotEarlyFlowAndBasisComposite,
    )
finally:
    sys.modules["strategy"] = _WRAPPER

_components = tuple(
    item.strip()
    for item in os.environ.get("CANDIDATE05_COMPONENTS", "").split(",")
    if item.strip()
)
if tuple(sorted(_components)) != _components or len(set(_components)) != len(_components):
    raise RuntimeError(
        "CANDIDATE05_COMPONENTS must be a unique sorted comma-separated subset",
    )
_candidates = {
    ("v55",): SpotLedPriceDiscoveryStrategy,
    ("v56",): EarlyFlowFirstRetraceStrategy,
    ("v58",): ForcedBasisReversionStrategy,
    ("v55", "v56"): SpotAndEarlyFlowComposite,
    ("v55", "v58"): SpotAndBasisComposite,
    ("v56", "v58"): EarlyFlowAndBasisComposite,
    ("v55", "v56", "v58"): SpotEarlyFlowAndBasisComposite,
}
if _components not in _candidates:
    raise RuntimeError(
        "no promoted component selection supplied: "
        f"{_components}; allowed={sorted(_candidates)}",
    )

LiquidityResponseConfig = _BASE.LiquidityResponseConfig
LiquidityResponseStrategy = _candidates[_components]

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
