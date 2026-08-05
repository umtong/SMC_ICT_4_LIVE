"""Rejection, acceptance, and no-trade state machine."""
from __future__ import annotations
from typing import Any, Iterable
from model import Bar,Direction,EntryPlan,ExitReason,PoolSide,Scenario,ScenarioKind,ScenarioState,StrategyConfig,SweepObservation
from liquidity_detector import LiquidityDetector
from strategy_common import Emit,close_location,ratio

class AuctionScenarioEngine:
    """Converts one pool sweep into rejection, acceptance, or explicit no-trade."""
    TERMINAL={ScenarioState.CLOSED,ScenarioState.INVALIDATED,ScenarioState.EXPIRED}
    def __init__(self,config:StrategyConfig,detector:LiquidityDetector,emit:Emit)->None:
        self.config=config; self.detector=detector; self.emit=emit
        self.scenario:Scenario|None=None; self.entry_plan:EntryPlan|None=None; self._sequence=0
    @property
    def busy(self)->bool:return self.scenario is not None and self.scenario.state not in self.TERMINAL
    def reset_if_terminal(self)->None:
        if self.scenario and self.scenario.state in self.TERMINAL:self.scenario=None;self.entry_plan=None
    def consider_sweeps(self,sweeps:Iterable[SweepObservation],bar_index:int)->None:
        if self.busy:return
        candidates=list(sweeps)
        if not candidates:return
        candidates.sort(key=lambda s:(-len(s.pool.details.get('confluence',[])),s.penetration_atr,s.pool.pool_id))
        sweep=candidates[0];self._sequence+=1;scenario_id=f"C03-{sweep.observed_time_ns}-{self._sequence:04d}"
        self.scenario=Scenario(scenario_id,sweep,ScenarioState.CLASSIFYING,bar_index,bar_index)
        self.entry_plan=None
        self.emit(scenario_id=scenario_id,event_type="SCENARIO_STARTED",event_time_ns=sweep.observed_time_ns,
                  observed_time_ns=sweep.observed_time_ns,previous_state=ScenarioState.IDLE.value,
                  next_state=ScenarioState.CLASSIFYING.value,reason_code="SWEEP_REQUIRES_AUCTION_CLASSIFICATION",
                  reference_price=sweep.pool.price,details={"pool_id":sweep.pool.pool_id,"pool_kind":sweep.pool.kind.value,
                  "pool_side":sweep.pool.side.value,"initial_bias":sweep.initial_bias.value if sweep.initial_bias else None,
                  "pre_break_price":sweep.pre_break_price,"sweep_extreme":sweep.extreme_price})
    def on_bar(self,bar:Bar,index:int)->EntryPlan|None:
        scenario=self.scenario
        if scenario is None:return None
        if scenario.state is ScenarioState.CLASSIFYING:
            scenario.evidence_bars.append(bar);scenario.last_index=index
            self._classify_and_arm(scenario,bar,index)
            if scenario.state is ScenarioState.CLASSIFYING and index-scenario.created_index>=self.config.confirm_bars:
                self._finish(ScenarioState.EXPIRED,"NO_DOMINANT_REJECTION_OR_ACCEPTANCE",bar)
            return None
        if scenario.state is ScenarioState.ENTRY_ARMED:
            plan=self.entry_plan
            assert plan is not None
            if index>plan.expires_index:self._finish(ScenarioState.EXPIRED,"RETEST_DID_NOT_OCCUR",bar);return None
            if self._pre_entry_invalidated(plan,bar):self._finish(ScenarioState.INVALIDATED,"THESIS_FAILED_BEFORE_RETEST",bar);return None
            if bar.low<=plan.entry_price<=bar.high:
                previous=scenario.state;scenario.state=ScenarioState.POSITION_ACTIVE;scenario.last_index=index
                self.emit(scenario_id=scenario.scenario_id,event_type="ENTRY_TRIGGERED",event_time_ns=bar.close_time_ns,
                          observed_time_ns=bar.close_time_ns,previous_state=previous.value,next_state=ScenarioState.POSITION_ACTIVE.value,
                          reason_code="FIRST_CAUSAL_RETEST",reference_price=plan.entry_price,
                          details={"kind":plan.kind.value,"direction":plan.direction.value,"entry":plan.entry_price,
                          "stop":plan.stop_price,"target":plan.target_price})
                return plan
        return None
    def mark_closed(self,bar:Bar,reason:ExitReason)->None:
        if self.scenario is None:return
        previous=self.scenario.state;self.scenario.state=ScenarioState.CLOSED
        self.emit(scenario_id=self.scenario.scenario_id,event_type="POSITION_CLOSED",event_time_ns=bar.close_time_ns,
                  observed_time_ns=bar.close_time_ns,previous_state=previous.value,next_state=ScenarioState.CLOSED.value,
                  reason_code=reason.value,reference_price=bar.close,details={})
    def _features(self,scenario:Scenario,bar:Bar)->dict[str,float|bool]:
        sweep=scenario.sweep;evidence=scenario.evidence_bars
        total=sum(b.volume for b in evidence);flow=ratio(sum(b.signed_volume for b in evidence),total)
        atr=sweep.atr;body=ratio(bar.body,bar.range);location=close_location(bar)
        prior=self.detector.bars[max(0,sweep.bar_index-self.config.micro_lookback):sweep.bar_index]
        if sweep.pool.side is PoolSide.HIGH:
            reject_reclaim=bar.close<sweep.pool.price;reject_structure=bar.close<sweep.pre_break_price
            reject_disp=bar.range>=self.config.displacement_atr*atr and body>=self.config.displacement_body_fraction and location<=self.config.displacement_close_fraction
            reject_flow=flow<=-self.config.flow_imbalance_threshold
            accept_outside=len(evidence)>=self.config.acceptance_hold_bars and all(b.close>=sweep.pool.price for b in evidence[-self.config.acceptance_hold_bars:])
            accept_structure=bar.close>max(b.high for b in prior);accept_disp=bar.range>=self.config.displacement_atr*atr and body>=self.config.displacement_body_fraction and location>=1-self.config.displacement_close_fraction
            accept_flow=flow>=self.config.flow_imbalance_threshold
        else:
            reject_reclaim=bar.close>sweep.pool.price;reject_structure=bar.close>sweep.pre_break_price
            reject_disp=bar.range>=self.config.displacement_atr*atr and body>=self.config.displacement_body_fraction and location>=1-self.config.displacement_close_fraction
            reject_flow=flow>=self.config.flow_imbalance_threshold
            accept_outside=len(evidence)>=self.config.acceptance_hold_bars and all(b.close<=sweep.pool.price for b in evidence[-self.config.acceptance_hold_bars:])
            accept_structure=bar.close<min(b.low for b in prior);accept_disp=bar.range>=self.config.displacement_atr*atr and body>=self.config.displacement_body_fraction and location<=self.config.displacement_close_fraction
            accept_flow=flow<=-self.config.flow_imbalance_threshold
        return {"flow_ratio":flow,"body_fraction":body,"close_location":location,
                "rejection_reclaim":reject_reclaim,"rejection_structure":reject_structure,
                "rejection_displacement":reject_disp,"rejection_flow":reject_flow,
                "acceptance_outside":accept_outside,"acceptance_structure":accept_structure,
                "acceptance_displacement":accept_disp,"acceptance_flow":accept_flow}
    def _classify_and_arm(self,scenario:Scenario,bar:Bar,index:int)->None:
        features=self._features(scenario,bar)
        rejection=all(bool(features[k]) for k in ('rejection_reclaim','rejection_structure','rejection_displacement','rejection_flow'))
        acceptance=all(bool(features[k]) for k in ('acceptance_outside','acceptance_structure','acceptance_displacement','acceptance_flow'))
        if rejection==acceptance:return
        kind=ScenarioKind.REJECTION if rejection else ScenarioKind.ACCEPTANCE
        direction=scenario.sweep.rejection_direction if rejection else scenario.sweep.acceptance_direction
        plan=self._build_plan(scenario,kind,direction,bar,index,features)
        if plan is None:self._finish(ScenarioState.INVALIDATED,"NO_CAUSAL_TARGET_WITH_POSITIVE_NET_RR",bar);return
        previous=scenario.state;scenario.kind=kind;scenario.direction=direction;scenario.displacement_index=index
        scenario.entry_price=plan.entry_price;scenario.stop_price=plan.stop_price;scenario.target_price=plan.target_price
        scenario.armed_index=index;scenario.state=ScenarioState.ENTRY_ARMED;scenario.last_index=index;self.entry_plan=plan
        self.emit(scenario_id=scenario.scenario_id,event_type="SCENARIO_CONFIRMED",event_time_ns=bar.close_time_ns,
                  observed_time_ns=bar.close_time_ns,previous_state=previous.value,next_state=ScenarioState.ENTRY_ARMED.value,
                  reason_code=f"{kind.value}_DISPLACEMENT_AND_FLOW",reference_price=plan.entry_price,
                  details={"kind":kind.value,"direction":direction.value,"entry":plan.entry_price,"stop":plan.stop_price,
                  "target":plan.target_price,**features})
    def _build_plan(self,scenario:Scenario,kind:ScenarioKind,direction:Direction,bar:Bar,index:int,
                    features:dict[str,Any])->EntryPlan|None:
        entry=bar.close+self.config.entry_retrace_fraction*(bar.open-bar.close);atr=scenario.sweep.atr
        if kind is ScenarioKind.REJECTION:
            stop=scenario.sweep.extreme_price+self.config.stop_buffer_atr*atr if direction is Direction.SHORT else scenario.sweep.extreme_price-self.config.stop_buffer_atr*atr
        else:
            stop=scenario.sweep.pool.price-self.config.stop_buffer_atr*atr if direction is Direction.LONG else scenario.sweep.pool.price+self.config.stop_buffer_atr*atr
        if direction.sign*(entry-stop)<=0:return None
        targets=self.detector.targets(direction,entry,bar.close_time_ns)
        counterpart=scenario.sweep.pool.counterpart_price
        if counterpart is not None and direction.sign*(counterpart-entry)>0:targets.append(counterpart)
        viable=[]
        for raw in set(targets):
            target=raw-self.config.target_buffer_atr*atr if direction is Direction.LONG else raw+self.config.target_buffer_atr*atr
            gross=direction.sign*(target-entry)
            if gross>0 and self._net_rr(entry,stop,target,direction)>=self.config.min_net_reward_risk:viable.append((gross,target))
        if not viable:return None
        target=min(viable,key=lambda item:item[0])[1]
        return EntryPlan(scenario.scenario_id,kind,direction,entry,stop,target,index,index+self.config.entry_wait_bars,atr,
                         scenario.sweep.pool.pool_id,dict(features))
    def _net_rr(self,entry:float,stop:float,target:float,direction:Direction)->float:
        fee=self.config.taker_fee_bps/10_000;slip=self.config.slippage_bps/10_000
        loss=abs(entry-stop)+(entry+stop)*(fee+slip);reward=direction.sign*(target-entry)-(entry+target)*(fee+slip)
        return reward/loss if loss>0 else -1
    def _pre_entry_invalidated(self,plan:EntryPlan,bar:Bar)->bool:
        buffer=self.config.invalidation_close_atr*plan.atr
        if plan.kind is ScenarioKind.REJECTION:
            return bar.close>plan.stop_price-buffer if plan.direction is Direction.SHORT else bar.close<plan.stop_price+buffer
        return bar.close<plan.stop_price if plan.direction is Direction.LONG else bar.close>plan.stop_price
    def _finish(self,state:ScenarioState,reason:str,bar:Bar)->None:
        assert self.scenario is not None
        previous=self.scenario.state;self.scenario.state=state;self.scenario.reason=reason
        self.emit(scenario_id=self.scenario.scenario_id,event_type="SCENARIO_TERMINATED",event_time_ns=bar.close_time_ns,
                  observed_time_ns=bar.close_time_ns,previous_state=previous.value,next_state=state.value,
                  reason_code=reason,reference_price=bar.close,details={})
