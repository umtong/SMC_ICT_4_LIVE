"""Bug-compatible wrapper for the frozen second-touch policy.

The v2 policy itself is unchanged.  Its diagnostic records include ``ts_event``
and the calls also pass the timestamp positionally to the shared ``_event``
helper.  Python rejects that before the strategy can be evaluated.  This wrapper
removes only the duplicate logging keyword after verifying it equals the
positional timestamp, then delegates to the original implementation.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any


_BASE_PATH = (
    Path(__file__).resolve().parents[1]
    / "candidate-55"
    / "strategy_inventory_release_second_touch.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_inventory_release_second_touch_v2_base",
    _BASE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load second-touch v2 policy: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


Candidate35Config = _BASE.Candidate35Config
_cost_after_net_r = _BASE._cost_after_net_r


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """The exact v2 trading policy with a diagnostic-call compatibility fix."""

    def _event(self, *args: Any, **kwargs: Any) -> None:
        if len(args) >= 2 and "ts_event" in kwargs:
            duplicate = int(kwargs.pop("ts_event"))
            positional = int(args[1])
            if duplicate != positional:
                raise RuntimeError(
                    "diagnostic timestamp disagrees with positional event timestamp: "
                    f"{duplicate} != {positional}"
                )
        super()._event(*args, **kwargs)


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_cost_after_net_r",
]
