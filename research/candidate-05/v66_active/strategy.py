"""Evidence-selected cooperative composite over v55/v56/v58/v59/v62.

The workflow supplies a unique sorted ``CANDIDATE05_COMPONENTS`` subset. The
base order gives the scheduled post-funding family priority over the generic
basis observer after the one shared v46 bar-processing core:

v59 -> v55 -> v58 -> v62 -> v56 -> v46 core, then unwind as
v62 funding observer -> v58 basis observer -> v55 context -> v59 boundary.
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
_SPEC = importlib.util.spec_from_file_location("_candidate05_v66_base", _BASE_PATH)
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
    from strategy_v59_spot_boundary_retest import SpotBoundaryRetestStrategy  # noqa: E402
    from strategy_v62_post_funding_reset import PostFundingForcedResetStrategy  # noqa: E402
finally:
    sys.modules["strategy"] = _WRAPPER

_components = tuple(
    item.strip()
    for item in os.environ.get("CANDIDATE05_COMPONENTS", "").split(",")
    if item.strip()
)
if tuple(sorted(_components)) != _components or len(set(_components)) != len(_components):
    raise RuntimeError("CANDIDATE05_COMPONENTS must be unique and sorted")
_allowed = {"v55", "v56", "v58", "v59", "v62"}
if not _components or not set(_components).issubset(_allowed):
    raise RuntimeError(f"invalid promoted component selection: {_components}")
_ordered_bases = [
    ("v59", SpotBoundaryRetestStrategy),
    ("v55", SpotLedPriceDiscoveryStrategy),
    ("v58", ForcedBasisReversionStrategy),
    ("v62", PostFundingForcedResetStrategy),
    ("v56", EarlyFlowFirstRetraceStrategy),
]
_bases = tuple(candidate for name, candidate in _ordered_bases if name in _components)
LiquidityResponseStrategy = type(
    "PromotedFiveFamilyComposite",
    _bases,
    {
        "__doc__": "Dynamically composed from independently OOS-promoted families.",
        "PROMOTED_COMPONENTS": _components,
    },
)
LiquidityResponseConfig = _BASE.LiquidityResponseConfig

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
