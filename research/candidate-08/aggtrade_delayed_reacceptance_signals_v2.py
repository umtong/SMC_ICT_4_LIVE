"""Safe-observability revision of delayed boundary reacceptance.

Revision V2 fixes implementation boundaries before performance evidence:

* an unobservable or non-finite flow-response row cannot be cast to an integer direction;
* target-touch classification uses the exact external-level enum rather than a string value; and
* every emitted event is stamped with the V2 implementation revision.

The economic sequence, thresholds, expiry, target, stop and cost geometry are unchanged from V1.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

import aggtrade_delayed_reacceptance_signals as base
from aggtrade_flow_response import FlowResponseState
from range_fvg_logic import LevelKind


IMPLEMENTATION_REVISION = "CAUSAL_DELAYED_BOUNDARY_REACCEPTANCE_V2_SAFE_OBSERVABILITY"
base.IMPLEMENTATION_REVISION = IMPLEMENTATION_REVISION


def _observable_feature(row: pd.Series) -> bool:
    if str(row["flow_response_state"]) == FlowResponseState.UNOBSERVABLE.value:
        return False
    return all(
        isfinite(float(row[name]))
        for name in base._REQUIRED_FEATURE_COLUMNS
        if name != "flow_response_state"
    )


def _initial_response_qualifies(
    feature: pd.Series,
    *,
    outward: int,
    close: float,
    boundary: float,
    initial_mode: str,
) -> bool:
    if initial_mode not in base.INITIAL_MODES:
        raise ValueError(f"invalid initial response mode: {initial_mode!r}")
    if not _observable_feature(feature):
        return False
    direction_matches = int(np.sign(float(feature["flow_direction"]))) == outward
    if not direction_matches or not base._outside(close, boundary, outward):
        return False
    if initial_mode == base.ABLATION_INITIAL_MODE:
        return True
    return str(feature["flow_response_state"]) == FlowResponseState.INITIATIVE_RESPONSE.value


def _reacceptance_qualifies(
    feature: pd.Series,
    *,
    outward: int,
    close: float,
    boundary: float,
    counter_high: float,
    counter_low: float,
) -> bool:
    if not _observable_feature(feature):
        return False
    if str(feature["flow_response_state"]) != FlowResponseState.INITIATIVE_RESPONSE.value:
        return False
    if int(np.sign(float(feature["flow_direction"]))) != outward:
        return False
    if not base._outside(close, boundary, outward):
        return False
    return close > counter_high if outward > 0 else close < counter_low


def _target_was_touched(
    data: pd.DataFrame,
    *,
    start_position: int,
    end_position: int,
    target: Any,
) -> bool:
    if end_position < start_position:
        return False
    observed = data.iloc[start_position : end_position + 1]
    if target.kind is LevelKind.HIGH:
        return float(observed["high"].max()) >= float(target.level)
    if target.kind is LevelKind.LOW:
        return float(observed["low"].min()) <= float(target.level)
    raise RuntimeError(f"unknown external target kind: {target.kind!r}")


base._observable_feature = _observable_feature
base._initial_response_qualifies = _initial_response_qualifies
base._reacceptance_qualifies = _reacceptance_qualifies
base._target_was_touched = _target_was_touched

ABLATION_INITIAL_MODE = base.ABLATION_INITIAL_MODE
BASE_INITIAL_MODE = base.BASE_INITIAL_MODE
DelayedReacceptanceConfig = base.DelayedReacceptanceConfig
REACCEPTANCE_FAMILY = base.REACCEPTANCE_FAMILY
build_delayed_reacceptance_signals = base.build_delayed_reacceptance_signals


__all__ = [
    "ABLATION_INITIAL_MODE",
    "BASE_INITIAL_MODE",
    "DelayedReacceptanceConfig",
    "IMPLEMENTATION_REVISION",
    "REACCEPTANCE_FAMILY",
    "build_delayed_reacceptance_signals",
]
