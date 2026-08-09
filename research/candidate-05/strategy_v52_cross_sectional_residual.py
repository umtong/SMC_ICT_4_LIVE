"""Candidate 05 v52: symmetric four-asset residual convergence.

Each asset is compared with the median ATR-normalized five-minute return of the
other three assets, using observations strictly earlier than its current bar.
A 2.5 robust-MAD idiosyncratic excursion must begin converging while OI is not
expanding and local tail flow/depth turn with the convergence.  The inherited
v26 CHoCH/retest, structural stop, real liquidity target, costs, 3% current-NAV
risk and NautilusTrader account lifecycle remain unchanged.  The existing
shared-account global slot admits only one completed candidate.
"""
from __future__ import annotations

from collections import deque
import math
from statistics import median
from typing import Any

from relative_value_context import completed_history,publish,reset
from strategy_base import PendingSetup
from strategy_v41_competing_auction import _construct,_finite
from strategy_v47_relative_value import RelativeValueDislocationStrategy,_BASE,_symbol


ALL_SYMBOLS=('BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT')
WINDOW=180
MIN_OBSERVATIONS=90
ROBUST_Z=2.5


def _normalized_return(history:tuple[Any,...],bars:int)->float:
    if len(history)<=bars:return math.nan
    first=float(history[-(bars+1)].close);last=float(history[-1].close);atr=float(history[-1].atr)
    if first<=0.0 or last<=0.0 or atr<=0.0:return math.nan
    return math.log(last/first)/(atr/last)


def _robust_z(values:deque[float],current:float)->float:
    finite=[value for value in values if math.isfinite(value)]
    if len(finite)<MIN_OBSERVATIONS:return math.nan
    center=median(finite);mad=median(abs(value-center) for value in finite);scale=max(1.4826*mad,1e-8);return (current-center)/scale


class CrossSectionalResidualStrategy(RelativeValueDislocationStrategy):
    """Trade the first fully confirmed idiosyncratic convergence among four assets."""

    def __init__(self,config:Any)->None:
        super().__init__(config)
        self.v52_residuals:deque[float]=deque(maxlen=WINDOW)
        self.v52_previous_residual=math.nan
        self.v52_last_signal_index=-10_000
        self.diagnostics.update({'v52_peer_context_ready':0,'v52_extremes':0,'v52_inflections':0,'v52_oi_contraction_pass':0,'v52_flow_depth_pass':0,'v52_setups':0,'v52_same_timestamp_peer_uses':0})

    def on_start(self)->None:
        if self.v47_symbol=='BTCUSDT':reset()
        _BASE.on_start(self)

    def on_bar(self,bar)->None:
        # Bypass v47's BTC-only arming while retaining the mature v26 state machine.
        _BASE.on_bar(self,bar)
        if not self.bars:return
        row=self.bars[-1];atr=_finite(self._atr()) if len(self.bars)>self.config.atr_period else math.nan
        publish(symbol=self.v47_symbol,ts=int(row['ts']),close=float(row['close']),atr=atr)
        self._maybe_arm_cross_sectional(row)

    def _peer_state(self,ts:int)->tuple[float,float]|None:
        peer5=[];peer1=[]
        for symbol in ALL_SYMBOLS:
            if symbol==self.v47_symbol:continue
            history=completed_history(symbol,before_ts=ts,count=8)
            if len(history)<6:return None
            if history[-1].ts>=ts:self.diagnostics['v52_same_timestamp_peer_uses']+=1;return None
            five=_normalized_return(history,5);one=_normalized_return(history,1)
            if not math.isfinite(five) or not math.isfinite(one):return None
            peer5.append(five);peer1.append(one)
        return median(peer5),median(peer1)

    def _own_normalized_return(self,bars:int)->float:
        if len(self.bars)<=bars:return math.nan
        first=float(self.bars[-(bars+1)]['close']);last=float(self.bars[-1]['close']);atr=_finite(self._atr())
        if first<=0.0 or last<=0.0 or not math.isfinite(atr) or atr<=0.0:return math.nan
        return math.log(last/first)/(atr/last)

    def _maybe_arm_cross_sectional(self,row:dict[str,float|int])->None:
        if self.pending is not None or self.entry_pending or not self.portfolio.is_flat(self.config.instrument_id) or not self._in_evaluation(int(row['ts'])) or not self._features_ready(int(row['ts'])) or self.bar_index-self.last_entry_index<self.config.cooldown_bars or self.bar_index-self.v52_last_signal_index<self.config.rejection_confirmation_bars or len(self.bars)<max(self.config.atr_period+2,8):return
        peer=self._peer_state(int(row['ts']))
        if peer is None:return
        peer5,peer1=peer;own5=self._own_normalized_return(5);own1=self._own_normalized_return(1)
        if not math.isfinite(own5) or not math.isfinite(own1):return
        residual=own5-peer5;z=_robust_z(self.v52_residuals,residual);previous=self.v52_previous_residual;self.v52_residuals.append(residual);self.v52_previous_residual=residual;self.diagnostics['v52_peer_context_ready']+=1
        if not math.isfinite(z) or abs(z)<ROBUST_Z:return
        self.diagnostics['v52_extremes']+=1
        if not math.isfinite(previous) or residual==0.0 or previous==0.0 or math.copysign(1.0,residual)!=math.copysign(1.0,previous) or abs(residual)>=abs(previous):return
        side=-1 if residual>0.0 else 1
        if side*(own1-peer1)<=0.0 or side*peer1<-0.25:return
        self.diagnostics['v52_inflections']+=1
        oi=_finite(self._feature('oi_change_15m'))
        if not math.isfinite(oi) or oi>0.0:return
        self.diagnostics['v52_oi_contraction_pass']+=1
        values={name:_finite(self._feature(name)) for name in ('flow_15s','flow_60s','flow_3m','depth_imbalance_1','efficiency_60s','notional_burst')}
        if not all(math.isfinite(value) for value in values.values()):return
        if not (side*values['flow_15s']>0.0 and side*(values['flow_15s']-values['flow_60s'])>0.0 and side*values['depth_imbalance_1']>0.0 and values['efficiency_60s']>=0.10 and values['notional_burst']>=1.0):return
        self.diagnostics['v52_flow_depth_pass']+=1
        atr=_finite(self._atr());recent=list(self.bars)[-6:-1]
        if not math.isfinite(atr) or atr<=0.0 or not recent:return
        structure=max(float(item['high']) for item in recent) if side>0 else min(float(item['low']) for item in recent);extreme=min(float(item['low']) for item in list(self.bars)[-3:]) if side>0 else max(float(item['high']) for item in list(self.bars)[-3:])
        self.scenario_counter+=1;details={'branch':'CROSS_SECTIONAL_RESIDUAL_REJECTION','symbol':self.v47_symbol,'residual':residual,'residual_z':z,'own_normalized_5m':own5,'peer_normalized_5m':peer5,'own_normalized_1m':own1,'peer_normalized_1m':peer1,'flow_15s':values['flow_15s'],'flow_60s':values['flow_60s'],'flow_3m':values['flow_3m'],'efficiency_60s':values['efficiency_60s'],'notional_burst':values['notional_burst'],'depth_imbalance_1':values['depth_imbalance_1'],'oi_change_15m':oi,'pool_source':'FOUR_ASSET_ROBUST_RESIDUAL','pool_age_minutes':5,'penetration_atr':abs(residual)}
        self.pending=_construct(PendingSetup,scenario_id=f'v52-{self.v47_symbol}-{self.scenario_counter:07d}',branch='REJECTION',side=side,swept_kind='LOW' if side>0 else 'HIGH',pool_id=f'cross-sectional-{self.v47_symbol}-{int(row["ts"])}',pool_level=float(row['close']),created_index=self.bar_index,created_ts=int(row['ts']),expires_index=self.bar_index+self.config.rejection_confirmation_bars,sweep_extreme=extreme,structure=structure,atr=atr,hold_count=0,retrace_armed=False,details=details);self.v52_last_signal_index=self.bar_index;self.diagnostics['rejection_setups']+=1;self.diagnostics['v52_setups']+=1


CandidateStrategy=CrossSectionalResidualStrategy
StrategyClass=CrossSectionalResidualStrategy
CrossAssetRepricingGateStrategy=CrossSectionalResidualStrategy
