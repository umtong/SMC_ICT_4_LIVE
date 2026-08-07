"""Candidate 05 v48: session value auction mean reversion and continuation.

The current completed eight-hour funding session defines volume-weighted fair
value and dispersion.  Balanced auctions may reverse only after a 2.5-sigma
excursion re-enters and order flow turns.  Directional auctions may continue
only on the first defended pullback toward value with expanding OI.  The mature
v26 confirmation/retest, costs, structural stop, target, 3% NAV risk and
NautilusTrader lifecycle remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from strategy_base import PendingSetup
try:
    from strategy_v6 import PositionBuildingSetup
except ImportError:  # pragma: no cover
    PositionBuildingSetup = None  # type: ignore[assignment]
import strategy_v26 as _v26
from strategy_v41_competing_auction import _construct, _finite


SESSION_BARS = 8 * 60
MIN_SESSION_BARS = 60
FADE_Z = 2.5
BALANCED_MAX_EFFICIENCY = 1.0 / 3.0
DIRECTIONAL_MIN_EFFICIENCY = 1.0 / 2.0


def _base_class() -> type:
    found=[value for value in vars(_v26).values() if isinstance(value,type) and value.__module__==_v26.__name__ and value.__name__.endswith('Strategy')]
    if len(found)!=1: raise RuntimeError(f'expected one v26 strategy, found {[value.__name__ for value in found]}')
    return found[0]


_BASE=_base_class()


@dataclass(frozen=True,slots=True)
class SessionValue:
    vwap: float
    deviation: float
    z: float
    path_efficiency: float
    path_direction: int
    session_high: float
    session_low: float
    bars: int


class SessionValueAuctionStrategy(_BASE):
    """Route value rejection and value acceptance into inherited entry paths."""

    def __init__(self,config:Any)->None:
        super().__init__(config)
        self.v48_previous_z=math.nan
        self.v48_trend_pullback_used_session:int|None=None
        self.v48_last_session_key:int|None=None
        self.diagnostics.update({'v48_value_ready':0,'v48_fade_excursions':0,'v48_fade_setups':0,'v48_directional_sessions':0,'v48_trend_pullback_setups':0,'v48_intermediate_regime':0})

    def _detect_sweep(self,row:dict[str,float|int],previous_close:float)->None:
        # v48 is the sole entry detector; inherited pool maintenance and targets remain.
        return

    @staticmethod
    def _session_key(ts:int)->int:
        return ts//(8*60*60*1_000_000_000)

    def _value_state(self)->SessionValue|None:
        if len(self.bars)<MIN_SESSION_BARS:return None
        key=self._session_key(int(self.bars[-1]['ts']))
        rows=[row for row in list(self.bars)[-SESSION_BARS:] if self._session_key(int(row['ts']))==key]
        if len(rows)<MIN_SESSION_BARS:return None
        weights=[max(float(row['volume']),0.0) for row in rows]
        total=sum(weights)
        if total<=0.0:return None
        typical=[(float(row['high'])+float(row['low'])+float(row['close']))/3.0 for row in rows]
        vwap=sum(price*weight for price,weight in zip(typical,weights,strict=True))/total
        variance=sum(weight*(price-vwap)**2 for price,weight in zip(typical,weights,strict=True))/total
        deviation=math.sqrt(max(variance,1e-18))
        close=float(rows[-1]['close'])
        z=(close-vwap)/deviation
        path=sum(abs(float(rows[index]['close'])-float(rows[index-1]['close'])) for index in range(1,len(rows)))
        net=float(rows[-1]['close'])-float(rows[0]['open'])
        efficiency=abs(net)/path if path>0.0 else 0.0
        direction=1 if net>0.0 else -1 if net<0.0 else 0
        return SessionValue(vwap,deviation,z,efficiency,direction,max(float(row['high']) for row in rows),min(float(row['low']) for row in rows),len(rows))

    def on_bar(self,bar)->None:
        super().on_bar(bar)
        if not self.bars:return
        row=self.bars[-1]
        if self.pending is not None or self.entry_pending or not self.portfolio.is_flat(self.config.instrument_id):return
        if not self._in_evaluation(int(row['ts'])) or not self._features_ready(int(row['ts'])):return
        if self.bar_index-self.last_entry_index<self.config.cooldown_bars:return
        state=self._value_state()
        if state is None:return
        self.diagnostics['v48_value_ready']+=1
        key=self._session_key(int(row['ts']))
        if key!=self.v48_last_session_key:
            self.v48_last_session_key=key; self.v48_trend_pullback_used_session=None; self.v48_previous_z=math.nan
        previous_z=self.v48_previous_z; self.v48_previous_z=state.z
        if state.path_efficiency<=BALANCED_MAX_EFFICIENCY:
            self._balanced_fade(row,state,previous_z)
        elif state.path_efficiency>=DIRECTIONAL_MIN_EFFICIENCY:
            self.diagnostics['v48_directional_sessions']+=1
            self._directional_pullback(row,state,key)
        else:
            self.diagnostics['v48_intermediate_regime']+=1

    def _flow_state(self,side:int)->tuple[bool,dict[str,float]]:
        values={name:_finite(self._feature(name)) for name in ('flow_15s','flow_60s','flow_3m','depth_imbalance_1','efficiency_60s','notional_burst','oi_change_15m')}
        if not all(math.isfinite(value) for value in values.values()):return False,values
        passed=side*values['flow_15s']>0.0 and side*(values['flow_15s']-values['flow_60s'])>0.0 and side*values['depth_imbalance_1']>0.0 and values['efficiency_60s']>=0.10 and values['notional_burst']>=1.0
        return passed,values

    def _balanced_fade(self,row:dict[str,float|int],state:SessionValue,previous_z:float)->None:
        if not math.isfinite(previous_z) or abs(previous_z)<FADE_Z or abs(state.z)>=abs(previous_z) or abs(state.z)>FADE_Z:return
        self.diagnostics['v48_fade_excursions']+=1
        side=-1 if previous_z>0.0 else 1
        passed,features=self._flow_state(side)
        if not passed or features['oi_change_15m']>0.0:return
        recent=list(self.bars)[-6:-1]
        if not recent:return
        atr=_finite(self._atr())
        if not math.isfinite(atr) or atr<=0.0:return
        structure=max(float(item['high']) for item in recent) if side>0 else min(float(item['low']) for item in recent)
        extreme=state.session_low if side>0 else state.session_high
        self.scenario_counter+=1
        details={'branch':'SESSION_VALUE_FADE','session_vwap':state.vwap,'session_deviation':state.deviation,'previous_z':previous_z,'current_z':state.z,'pool_source':'COMPLETED_8H_SESSION_VALUE','pool_age_minutes':state.bars,'penetration_atr':abs(float(row['close'])-state.vwap)/atr,**features}
        self.pending=_construct(PendingSetup,scenario_id=f'v48-fade-{self.scenario_counter:07d}',branch='REJECTION',side=side,swept_kind='LOW' if side>0 else 'HIGH',pool_id=f'session-value-{int(row["ts"])}',pool_level=state.vwap,created_index=self.bar_index,created_ts=int(row['ts']),expires_index=self.bar_index+self.config.rejection_confirmation_bars,sweep_extreme=extreme,structure=structure,atr=atr,hold_count=0,retrace_armed=False,details=details)
        self.diagnostics['rejection_setups']+=1; self.diagnostics['v48_fade_setups']+=1

    def _directional_pullback(self,row:dict[str,float|int],state:SessionValue,key:int)->None:
        if self.v48_trend_pullback_used_session==key or state.path_direction==0:return
        side=state.path_direction
        # First return from beyond one deviation into the 0.25-1.0 deviation value edge.
        if not math.isfinite(self.v48_previous_z) or side*state.z<0.25 or side*state.z>1.0:return
        passed,features=self._flow_state(side)
        if not passed or features['oi_change_15m']<=0.0 or side*features['flow_60s']<=0.0 or side*features['flow_3m']<0.0:return
        if PositionBuildingSetup is None or not hasattr(self,'position_building_setups'):return
        self.scenario_counter+=1
        details={'branch':'SESSION_VALUE_TREND_PULLBACK','session_vwap':state.vwap,'session_deviation':state.deviation,'current_z':state.z,'pool_source':'COMPLETED_8H_SESSION_VALUE','pool_age_minutes':state.bars,'penetration_atr':abs(float(row['close'])-state.vwap)/max(_finite(self._atr()),1e-12),**features}
        setup=_construct(PositionBuildingSetup,scenario_id=f'v48-trend-{self.scenario_counter:07d}',side=side,pool_level=state.vwap,created_index=self.bar_index,created_ts=int(row['ts']),expires_index=self.bar_index+self.config.acceptance_retrace_bars,hold_count=0,details=details)
        self.position_building_setups.append(setup); self.v48_trend_pullback_used_session=key; self.diagnostics['position_building_setups']+=1; self.diagnostics['v48_trend_pullback_setups']+=1


CandidateStrategy=SessionValueAuctionStrategy
StrategyClass=SessionValueAuctionStrategy
