"""Candidate 05 v49: multiscale intrinsic-time liquidity auction.

Two causal directional-change clocks, 0.5 ATR and 1.0 ATR, replace fixed swing
spacing.  Same-side clock agreement at an already-known pool may arm a
rejection.  Inside the large-clock trend, a small-clock counter change followed
by realignment may arm position-building continuation.  The inherited v26 path
still owns structure confirmation, first retrace, real liquidity target,
structural stop, costs, 3% NAV sizing and all NautilusTrader state.
"""
from __future__ import annotations

import math
from typing import Any

from directional_change_logic import DirectionalChangeState, aligned_change, trend_pullback_realignment
from strategy_base import PendingSetup
try:
    from strategy_v6 import PositionBuildingSetup
except ImportError:  # pragma: no cover
    PositionBuildingSetup=None  # type: ignore[assignment]
import strategy_v26 as _v26
from strategy_v41_competing_auction import _construct, _finite


def _base_class()->type:
    found=[value for value in vars(_v26).values() if isinstance(value,type) and value.__module__==_v26.__name__ and value.__name__.endswith('Strategy')]
    if len(found)!=1:raise RuntimeError(f'expected one v26 strategy, found {[value.__name__ for value in found]}')
    return found[0]


_BASE=_base_class()


class MultiScaleDirectionalChangeStrategy(_BASE):
    def __init__(self,config:Any)->None:
        super().__init__(config)
        self.v49_small=DirectionalChangeState(0.5)
        self.v49_large=DirectionalChangeState(1.0)
        self.v49_previous_small_change=0
        self.v49_last_event_index=-10_000
        self.diagnostics.update({'v49_small_changes':0,'v49_large_changes':0,'v49_aligned_reversals':0,'v49_pool_supported_reversals':0,'v49_reversal_setups':0,'v49_trend_realignments':0,'v49_trend_setups':0})

    def _detect_sweep(self,row:dict[str,float|int],previous_close:float)->None:
        return

    def on_bar(self,bar)->None:
        super().on_bar(bar)
        if not self.bars:return
        row=self.bars[-1]
        atr=_finite(self._atr())
        if not math.isfinite(atr) or atr<=0.0:return
        previous_small=self.v49_small.last_change_side
        small_change=self.v49_small.update(high=float(row['high']),low=float(row['low']),close=float(row['close']),atr=atr,index=self.bar_index)
        large_change=self.v49_large.update(high=float(row['high']),low=float(row['low']),close=float(row['close']),atr=atr,index=self.bar_index)
        if small_change:self.diagnostics['v49_small_changes']+=1
        if large_change:self.diagnostics['v49_large_changes']+=1
        if self.pending is not None or self.entry_pending or not self.portfolio.is_flat(self.config.instrument_id):
            self.v49_previous_small_change=small_change or self.v49_previous_small_change
            return
        if not self._in_evaluation(int(row['ts'])) or not self._features_ready(int(row['ts'])) or self.bar_index-self.last_entry_index<self.config.cooldown_bars or self.bar_index-self.v49_last_event_index<2:
            self.v49_previous_small_change=small_change or self.v49_previous_small_change
            return
        if small_change and aligned_change(self.v49_small,self.v49_large,side=small_change,max_delay=2):
            self.diagnostics['v49_aligned_reversals']+=1
            self._maybe_arm_reversal(row,atr,small_change)
        realignment=trend_pullback_realignment(large_mode=self.v49_large.mode,small_previous_change=self.v49_previous_small_change,small_current_change=small_change)
        if realignment:
            self.diagnostics['v49_trend_realignments']+=1
            self._maybe_arm_continuation(row,atr,realignment)
        if small_change:self.v49_previous_small_change=small_change

    def _flow_state(self,side:int,*,continuation:bool)->tuple[bool,dict[str,float]]:
        values={name:_finite(self._feature(name)) for name in ('flow_15s','flow_60s','flow_3m','depth_imbalance_1','efficiency_60s','notional_burst','oi_change_15m')}
        if not all(math.isfinite(value) for value in values.values()):return False,values
        if continuation:
            passed=values['oi_change_15m']>0.0 and side*values['flow_15s']>0.0 and side*values['flow_60s']>0.0 and side*values['flow_3m']>=0.0 and side*values['depth_imbalance_1']>0.0 and values['efficiency_60s']>=0.15 and values['notional_burst']>=1.0
        else:
            passed=values['oi_change_15m']<=0.0 and side*values['flow_15s']>0.0 and side*(values['flow_15s']-values['flow_60s'])>0.0 and side*values['depth_imbalance_1']>0.0 and values['efficiency_60s']>=0.10 and values['notional_burst']>=1.0
        return passed,values

    def _supporting_pool(self,side:int,extreme:float,atr:float)->Any|None:
        if side>0:
            pools=[pool for pool in self.active_pools.values() if pool.kind=='LOW' and extreme<=pool.level-self.config.sweep_min_penetration_atr*atr and self.bar_index-pool.created_index>=self.config.pool_min_age_bars]
            return max(pools,key=lambda pool:(pool.strength,pool.level)) if pools else None
        pools=[pool for pool in self.active_pools.values() if pool.kind=='HIGH' and extreme>=pool.level+self.config.sweep_min_penetration_atr*atr and self.bar_index-pool.created_index>=self.config.pool_min_age_bars]
        return max(pools,key=lambda pool:(pool.strength,-pool.level)) if pools else None

    def _maybe_arm_reversal(self,row:dict[str,float|int],atr:float,change_side:int)->None:
        # A down directional change confirms a short reversal from a high; an up
        # change confirms a long reversal from a low.
        side=change_side
        extreme=self.v49_large.last_extreme
        if not math.isfinite(extreme):return
        pool=self._supporting_pool(side,extreme,atr)
        if pool is None:return
        self.diagnostics['v49_pool_supported_reversals']+=1
        passed,features=self._flow_state(side,continuation=False)
        if not passed:return
        recent=list(self.bars)[-8:-1]
        if not recent:return
        structure=max(float(item['high']) for item in recent) if side>0 else min(float(item['low']) for item in recent)
        self._consume_pool(pool,row,'V49_DIRECTIONAL_CHANGE_LIQUIDITY_ACCESS')
        self.scenario_counter+=1
        details={'branch':'MULTISCALE_DC_REJECTION','pool_id':pool.pool_id,'pool_kind':pool.kind,'pool_level':pool.level,'pool_source':pool.source,'pool_strength':pool.strength,'pool_age_minutes':self.bar_index-pool.created_index,'penetration_atr':abs(extreme-pool.level)/atr,'dc_small_multiple':self.v49_small.multiple,'dc_large_multiple':self.v49_large.multiple,**features}
        self.pending=_construct(PendingSetup,scenario_id=f'v49-rev-{self.scenario_counter:07d}',branch='REJECTION',side=side,swept_kind=pool.kind,pool_id=pool.pool_id,pool_level=pool.level,created_index=self.bar_index,created_ts=int(row['ts']),expires_index=self.bar_index+self.config.rejection_confirmation_bars,sweep_extreme=extreme,structure=structure,atr=atr,hold_count=0,retrace_armed=False,details=details)
        self.v49_last_event_index=self.bar_index; self.diagnostics['rejection_setups']+=1; self.diagnostics['v49_reversal_setups']+=1

    def _maybe_arm_continuation(self,row:dict[str,float|int],atr:float,side:int)->None:
        if PositionBuildingSetup is None or not hasattr(self,'position_building_setups'):return
        passed,features=self._flow_state(side,continuation=True)
        if not passed:return
        pullback_extreme=self.v49_small.last_extreme
        if not math.isfinite(pullback_extreme):return
        # The pullback must have accessed an internal pool against the large trend.
        pool=self._supporting_pool(side,pullback_extreme,atr)
        if pool is None:return
        self._consume_pool(pool,row,'V49_TREND_PULLBACK_LIQUIDITY_ACCESS')
        self.scenario_counter+=1
        details={'branch':'MULTISCALE_DC_TREND_REALIGNMENT','pool_id':pool.pool_id,'pool_kind':pool.kind,'pool_level':pool.level,'pool_source':pool.source,'pool_strength':pool.strength,'pool_age_minutes':self.bar_index-pool.created_index,'penetration_atr':abs(pullback_extreme-pool.level)/atr,'dc_large_mode':self.v49_large.mode,**features}
        setup=_construct(PositionBuildingSetup,scenario_id=f'v49-trend-{self.scenario_counter:07d}',side=side,pool_level=pool.level,created_index=self.bar_index,created_ts=int(row['ts']),expires_index=self.bar_index+self.config.acceptance_retrace_bars,hold_count=0,details=details)
        self.position_building_setups.append(setup); self.v49_last_event_index=self.bar_index; self.diagnostics['position_building_setups']+=1; self.diagnostics['v49_trend_setups']+=1


CandidateStrategy=MultiScaleDirectionalChangeStrategy
StrategyClass=MultiScaleDirectionalChangeStrategy
