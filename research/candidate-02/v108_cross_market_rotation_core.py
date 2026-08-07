"""Cross-market classifier for derivative-led auction rotations in v108."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping

import pandas as pd

from v107_regime_rotation_core import (
    RegimeRotationConfig,
    build_rotation_signals as _build_v107_signals,
    build_state as _build_v107_state,
)
from v53_nt_core import CostConfig, RotationSignal


@dataclass(frozen=True, slots=True)
class CrossMarketRotationConfig(RegimeRotationConfig):
    spot_participation_max: float = 0.50

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CrossMarketRotationConfig":
        data=dict(values); data['auction_windows_5m']=tuple(int(v) for v in data['auction_windows_5m'])
        allowed=set(cls.__dataclass_fields__); unknown=sorted(set(data)-allowed)
        if unknown: raise ValueError(f"unknown v108 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        RegimeRotationConfig.__post_init__(self)
        if not 0 <= self.spot_participation_max <= 1.5:
            raise ValueError('v108 spot participation maximum must be in [0,1.5]')


def build_state(features: pd.DataFrame, config: CrossMarketRotationConfig) -> pd.DataFrame:
    required={'spot_log_ret_5m','perp_spot_log_basis','perp_spot_basis_change_5m'}
    missing=sorted(required-set(features.columns))
    if missing: raise ValueError(f'missing v108 cross-market columns: {missing}')
    return _build_v107_state(features,config)


def build_rotation_signals(
    *, state: pd.DataFrame, raw: pd.DataFrame,
    evaluation_start: pd.Timestamp, evaluation_end: pd.Timestamp,
    config: CrossMarketRotationConfig, costs: CostConfig,
) -> list[RotationSignal]:
    candidates=_build_v107_signals(state=state,raw=raw,evaluation_start=evaluation_start,evaluation_end=evaluation_end,config=config,costs=costs)
    result=[]
    for signal in candidates:
        t=pd.Timestamp(signal.source_feature_open_time_ns,unit='ns',tz='UTC')
        previous=t-pd.Timedelta(minutes=5)
        if t not in state.index or previous not in state.index: continue
        direction=1 if signal.side=='SELL' else -1
        perp_excursion=direction*float(state.at[previous,'log_ret_5m'])
        spot_excursion=direction*float(state.at[previous,'spot_log_ret_5m'])
        basis_expansion=direction*float(state.at[previous,'perp_spot_basis_change_5m'])
        basis_compression=-direction*float(state.at[t,'perp_spot_basis_change_5m'])
        if not all(math.isfinite(v) for v in (perp_excursion,spot_excursion,basis_expansion,basis_compression)):
            continue
        if perp_excursion <= 0: continue
        participation=max(0.0,spot_excursion)/max(perp_excursion,1e-12)
        if participation>config.spot_participation_max or basis_expansion<=0 or basis_compression<=0:
            continue
        details=dict(signal.details)
        details.update({
            'perp_excursion_return_5m':perp_excursion,
            'spot_excursion_return_5m':spot_excursion,
            'spot_participation_ratio':participation,
            'spot_participation_max':config.spot_participation_max,
            'directional_basis_expansion_5m':basis_expansion,
            'directional_basis_compression_5m':basis_compression,
        })
        result.append(replace(signal,scenario_id=signal.scenario_id.replace('v107-','v108-'),details=details))
    return result
