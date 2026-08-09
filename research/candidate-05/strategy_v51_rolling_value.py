"""Candidate 05 v51: rolling value-auction dual-regime system.

A completed rolling 30-minute volume-weighted auction defines fair value and
scale.  Low-efficiency auctions fade the first two-sigma liquidity excursion
which re-enters while OI contracts and micro flow/depth reverse.  High-
efficiency auctions continue only on their first pullback from a one-sigma
expansion while OI and multi-horizon flow keep building.  The inherited mature
v26 path handles CHoCH/retest, real liquidity targets, structural stops, costs,
3% current-NAV sizing and NautilusTrader execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import strategy_v26 as _v26
from strategy_base import PendingSetup
try:
    from strategy_v6 import PositionBuildingSetup
except ImportError:  # pragma: no cover
    PositionBuildingSetup=None  # type: ignore[assignment]
from strategy_v41_competing_auction import _construct,_finite


WINDOW=30
BALANCED_MAX_EFFICIENCY=1.0/3.0
DIRECTIONAL_MIN_EFFICIENCY=1.0/2.0
EXCURSION_Z=2.0
REENTRY_Z=1.5
TREND_EXPANSION_Z=1.0
TREND_PULLBACK_MIN_Z=0.20
TREND_PULLBACK_MAX_Z=0.75


def _base_class()->type:
    found=[value for value in vars(_v26).values() if isinstance(value,type) and value.__module__==_v26.__name__ and value.__name__.endswith('Strategy')]
    if len(found)!=1:raise RuntimeError(f'expected one v26 strategy, found {[value.__name__ for value in found]}')
    return found[0]


_BASE=_base_class()


@dataclass(frozen=True,slots=True)
class RollingValueState:
    mean:float
    deviation:float
    z:float
    efficiency:float
    direction:int
    high:float
    low:float


class RollingValueAuctionStrategy(_BASE):
    """Generate frequent but state-complete value-auction scenarios."""

    def __init__(self,config:Any)->None:
        super().__init__(config)
        self.v51_excursion_side=0
        self.v51_excursion_extreme=math.nan
        self.v51_trend_expanded_side=0
        self.v51_trend_used_anchor:int|None=None
        self.v51_last_signal_index=-10_000
        self.diagnostics.update({'v51_value_ready':0,'v51_balanced_windows':0,'v51_directional_windows':0,'v51_excursions_armed':0,'v51_fade_setups':0,'v51_trend_expansions':0,'v51_trend_setups':0,'v51_intermediate_windows':0})

    def _detect_sweep(self,row:dict[str,float|int],previous_close:float)->None:
        return

    def _state(self)->RollingValueState|None:
        if len(self.bars)<WINDOW:return None
        rows=list(self.bars)[-WINDOW:]
        weights=[max(float(row['volume']),0.0) for row in rows];total=sum(weights)
        if total<=0.0:return None
        typical=[(float(row['high'])+float(row['low'])+float(row['close']))/3.0 for row in rows]
        mean=sum(price*weight for price,weight in zip(typical,weights,strict=True))/total
        variance=sum(weight*(price-mean)**2 for price,weight in zip(typical,weights,strict=True))/total
        deviation=math.sqrt(max(variance,1e-18));close=float(rows[-1]['close']);z=(close-mean)/deviation
        path=sum(abs(float(rows[index]['close'])-float(rows[index-1]['close'])) for index in range(1,len(rows)));net=float(rows[-1]['close'])-float(rows[0]['open']);efficiency=abs(net)/path if path>0.0 else 0.0;direction=1 if net>0.0 else -1 if net<0.0 else 0
        return RollingValueState(mean,deviation,z,efficiency,direction,max(float(row['high']) for row in rows),min(float(row['low']) for row in rows))

    def _features(self,side:int,*,continuation:bool)->tuple[bool,dict[str,float]]:
        values={name:_finite(self._feature(name)) for name in ('flow_15s','flow_60s','flow_3m','depth_imbalance_1','efficiency_60s','notional_burst','oi_change_15m')}
        if not all(math.isfinite(value) for value in values.values()):return False,values
        if continuation:
            passed=values['oi_change_15m']>0.0 and side*values['flow_15s']>0.0 and side*values['flow_60s']>0.0 and side*values['flow_3m']>=0.0 and side*values['depth_imbalance_1']>0.0 and values['efficiency_60s']>=0.15 and values['notional_burst']>=1.0
        else:
            passed=values['oi_change_15m']<=0.0 and side*values['flow_15s']>0.0 and side*(values['flow_15s']-values['flow_60s'])>0.0 and side*values['depth_imbalance_1']>0.0 and values['efficiency_60s']>=0.10 and values['notional_burst']>=1.0
        return passed,values

    def on_bar(self,bar)->None:
        super().on_bar(bar)
        if not self.bars:return
        row=self.bars[-1];state=self._state()
        if state is None:return
        self.diagnostics['v51_value_ready']+=1
        if abs(state.z)<0.5:
            self.v51_excursion_side=0;self.v51_excursion_extreme=math.nan
        if state.efficiency<=BALANCED_MAX_EFFICIENCY:
            self.diagnostics['v51_balanced_windows']+=1;self._balanced(row,state)
        elif state.efficiency>=DIRECTIONAL_MIN_EFFICIENCY:
            self.diagnostics['v51_directional_windows']+=1;self._directional(row,state)
        else:self.diagnostics['v51_intermediate_windows']+=1

    def _available(self,row:dict[str,float|int])->bool:
        return self.pending is None and not self.entry_pending and self.portfolio.is_flat(self.config.instrument_id) and self._in_evaluation(int(row['ts'])) and self._features_ready(int(row['ts'])) and self.bar_index-self.last_entry_index>=self.config.cooldown_bars and self.bar_index-self.v51_last_signal_index>=2

    def _balanced(self,row:dict[str,float|int],state:RollingValueState)->None:
        if state.z>=EXCURSION_Z:
            if self.v51_excursion_side!=-1:self.diagnostics['v51_excursions_armed']+=1
            self.v51_excursion_side=-1;self.v51_excursion_extreme=max(self.v51_excursion_extreme if math.isfinite(self.v51_excursion_extreme) else -math.inf,float(row['high']));return
        if state.z<=-EXCURSION_Z:
            if self.v51_excursion_side!=1:self.diagnostics['v51_excursions_armed']+=1
            self.v51_excursion_side=1;self.v51_excursion_extreme=min(self.v51_excursion_extreme if math.isfinite(self.v51_excursion_extreme) else math.inf,float(row['low']));return
        side=self.v51_excursion_side
        if side==0 or not self._available(row) or abs(state.z)>REENTRY_Z:return
        passed,features=self._features(side,continuation=False)
        if not passed:return
        recent=list(self.bars)[-6:-1]
        if not recent:return
        atr=_finite(self._atr())
        if not math.isfinite(atr) or atr<=0.0 or not math.isfinite(self.v51_excursion_extreme):return
        structure=max(float(item['high']) for item in recent) if side>0 else min(float(item['low']) for item in recent)
        self.scenario_counter+=1;details={'branch':'ROLLING_VALUE_FADE','value_mean':state.mean,'value_deviation':state.deviation,'current_z':state.z,'pool_source':'ROLLING_30M_VALUE','pool_age_minutes':WINDOW,'penetration_atr':abs(self.v51_excursion_extreme-state.mean)/atr,**features}
        self.pending=_construct(PendingSetup,scenario_id=f'v51-fade-{self.scenario_counter:07d}',branch='REJECTION',side=side,swept_kind='LOW' if side>0 else 'HIGH',pool_id=f'rolling-value-{int(row["ts"])}',pool_level=state.mean,created_index=self.bar_index,created_ts=int(row['ts']),expires_index=self.bar_index+self.config.rejection_confirmation_bars,sweep_extreme=self.v51_excursion_extreme,structure=structure,atr=atr,hold_count=0,retrace_armed=False,details=details)
        self.v51_excursion_side=0;self.v51_excursion_extreme=math.nan;self.v51_last_signal_index=self.bar_index;self.diagnostics['rejection_setups']+=1;self.diagnostics['v51_fade_setups']+=1

    def _directional(self,row:dict[str,float|int],state:RollingValueState)->None:
        side=state.direction
        if side==0:return
        if side*state.z>=TREND_EXPANSION_Z:
            if self.v51_trend_expanded_side!=side:self.diagnostics['v51_trend_expansions']+=1
            self.v51_trend_expanded_side=side;return
        if self.v51_trend_expanded_side!=side or not self._available(row) or self.v51_trend_used_anchor==self.bar_index-WINDOW:return
        if side*state.z<TREND_PULLBACK_MIN_Z or side*state.z>TREND_PULLBACK_MAX_Z:return
        passed,features=self._features(side,continuation=True)
        if not passed or PositionBuildingSetup is None or not hasattr(self,'position_building_setups'):return
        self.scenario_counter+=1;details={'branch':'ROLLING_VALUE_TREND_PULLBACK','value_mean':state.mean,'value_deviation':state.deviation,'current_z':state.z,'pool_source':'ROLLING_30M_VALUE','pool_age_minutes':WINDOW,'penetration_atr':abs(float(row['close'])-state.mean)/max(_finite(self._atr()),1e-12),**features}
        setup=_construct(PositionBuildingSetup,scenario_id=f'v51-trend-{self.scenario_counter:07d}',side=side,pool_level=state.mean,created_index=self.bar_index,created_ts=int(row['ts']),expires_index=self.bar_index+self.config.acceptance_retrace_bars,hold_count=0,details=details);self.position_building_setups.append(setup);self.v51_trend_used_anchor=self.bar_index-WINDOW;self.v51_trend_expanded_side=0;self.v51_last_signal_index=self.bar_index;self.diagnostics['position_building_setups']+=1;self.diagnostics['v51_trend_setups']+=1


CandidateStrategy=RollingValueAuctionStrategy
StrategyClass=RollingValueAuctionStrategy
