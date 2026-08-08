"""Import adapter that keeps Candidate 16's top-level ``strategy`` module intact.

Candidate 16 v2 imports ``Candidate16Config`` from a module literally named
``strategy``.  Candidate 17 therefore loads its implementation under a unique
module name instead of shadowing that verified parent module.
"""
from __future__ import annotations

from importlib.util import module_from_spec
from importlib.util import spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType

_IMPL_NAME = "_candidate17_strategy_impl"
_IMPL_PATH = Path(__file__).with_name("strategy.py")


def _load_impl() -> ModuleType:
    cached = sys.modules.get(_IMPL_NAME)
    if cached is not None:
        return cached
    spec = spec_from_file_location(_IMPL_NAME, _IMPL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load Candidate 17 strategy from {_IMPL_PATH}")
    module = module_from_spec(spec)
    sys.modules[_IMPL_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_IMPL_NAME, None)
        raise
    return module


_impl = _load_impl()
Candidate17Config = _impl.Candidate17Config
Candidate17Strategy = _impl.Candidate17Strategy

__all__ = ["Candidate17Config", "Candidate17Strategy"]
