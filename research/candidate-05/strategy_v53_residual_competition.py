"""Candidate 05 v53: cross-sectional exhaustion versus catch-up competition.

v52 mean reversion is preserved for OI contraction.  When the common factor has
already moved at least 0.75 ATR and one asset lags in the opposite residual
direction, OI expansion plus aligned multi-horizon flow, depth and efficiency
may instead arm a catch-up CHoCH/retest.  Both states use the same inherited
v26 execution and global shared-account slot.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Any

from strategy_base import PendingSetup
from strategy_v41_competing_auction import _construct,_finite
from strategy_v52_cross_sectional_residual import (
    CrossSectionalResidualStrategy,_robust_z,
)


COMMON_FACTOR_MIN_ATR=0.75


class ResidualStateCompetitionStrategy(CrossSectionalResidualStrategy):
    """Compete deleveraging convergence with position-building catch-up."""

    def __init__(self,config:Any)->None:
        super().__init__(config)
        self.diagnostics.update({'v53_catchup_context':0,'v53_catchup_oi_pass':0,'v53_catchup_flow_pass':0,'v53_catchup_setups':0})

    def _maybe_arm_cross_sectional(self,row:dict[str,float|int])->None:
        previous=self.v52_previous_residual
        super()._maybe_arm_cross_sectional(row)
        if self.pending is not None or self.entry_pending or not self.portfolio.is_flat(self.config.instrument_id):return
        if not self._in_evaluation(int(row['ts'])) or not self._features_ready(int(row['ts'])) or self.bar_index-self.last_entry_index<self.config.cooldown_bars or self.bar_index-self.v52_last_signal_index<self.config.rejection_confirmation_bars:return
        peer=self._peer_state(int(row['ts']))
        if peer is None:return
        peer5,peer1=peer;own5=self._own_normalized_return(5);own1=self._own_normalized_return(1)
        if not all(math.isfinite(value) for value in (peer5,peer1,own5,own1,previous)):return
        residual=own5-peer5
        history=deque(list(self.v52_residuals)[:-1],maxlen=self.v52_residuals.maxlen)
        z=_robust_z(history,residual)
        if not math.isfinite(z) or abs(z)<2.5 or abs(peer5)<COMMON_FACTOR_MIN_ATR:return
        side=1 if peer5>0.0 else -1
        # Only a laggard can catch up: the residual is opposite the common move.
        if side*residual>=0.0 or abs(residual)>=abs(previous) or side*own1<=0.0 or side*peer1<=0.0:return
        self.diagnostics['v53_catchup_context']+=1
        oi=_finite(self._feature('oi_change_15m'))
        if not math.isfinite(oi) or oi<=0.0:return
        self.diagnostics['v53_catchup_oi_pass']+=1
        values={name:_finite(self._feature(name)) for name in ('flow_15s','flow_60s','flow_3m','depth_imbalance_1','efficiency_60s','notional_burst')}
        if not all(math.isfinite(value) for value in values.values()):return
        if not (side*values['flow_15s']>0.0 and side*values['flow_60s']>0.0 and side*values['flow_3m']>=0.0 and side*values['depth_imbalance_1']>0.0 and values['efficiency_60s']>=0.15 and values['notional_burst']>=1.0):return
        self.diagnostics['v53_catchup_flow_pass']+=1
        atr=_finite(self._atr());recent=list(self.bars)[-6:-1]
        if not math.isfinite(atr) or atr<=0.0 or not recent:return
        structure=max(float(item['high']) for item in recent) if side>0 else min(float(item['low']) for item in recent);extreme=min(float(item['low']) for item in list(self.bars)[-3:]) if side>0 else max(float(item['high']) for item in list(self.bars)[-3:])
        self.scenario_counter+=1;details={'branch':'CROSS_SECTIONAL_CATCHUP','symbol':self.v47_symbol,'residual':residual,'residual_z':z,'own_normalized_5m':own5,'peer_normalized_5m':peer5,'own_normalized_1m':own1,'peer_normalized_1m':peer1,'flow_15s':values['flow_15s'],'flow_60s':values['flow_60s'],'flow_3m':values['flow_3m'],'efficiency_60s':values['efficiency_60s'],'notional_burst':values['notional_burst'],'depth_imbalance_1':values['depth_imbalance_1'],'oi_change_15m':oi,'pool_source':'FOUR_ASSET_LAGGARD_CATCHUP','pool_age_minutes':5,'penetration_atr':abs(residual)}
        self.pending=_construct(PendingSetup,scenario_id=f'v53-{self.v47_symbol}-{self.scenario_counter:07d}',branch='REJECTION',side=side,swept_kind='LOW' if side>0 else 'HIGH',pool_id=f'catchup-{self.v47_symbol}-{int(row["ts"])}',pool_level=float(row['close']),created_index=self.bar_index,created_ts=int(row['ts']),expires_index=self.bar_index+self.config.rejection_confirmation_bars,sweep_extreme=extreme,structure=structure,atr=atr,hold_count=0,retrace_armed=False,details=details);self.v52_last_signal_index=self.bar_index;self.diagnostics['rejection_setups']+=1;self.diagnostics['v53_catchup_setups']+=1


CandidateStrategy=ResidualStateCompetitionStrategy
StrategyClass=ResidualStateCompetitionStrategy
CrossAssetRepricingGateStrategy=ResidualStateCompetitionStrategy
