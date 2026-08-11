"""Causal source scenarios with entry-time impulse targets."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from domain_v3 import (
    ArmedSetup,
    Candle,
    EasyChartOrderBlock,
    LiquidityPool,
    Side,
    TargetMode,
    detect_easychart_order_block,
    nearest_directional_pool,
)


@dataclass(frozen=True, slots=True)
class PivotConfirmation:
    center_index: int
    observed_index: int
    side: str
    level: float


def confirmed_pivot(candles: list[Candle], observed_index: int, span: int) -> PivotConfirmation | None:
    center = observed_index - span
    if center < span or observed_index >= len(candles):
        return None
    window = candles[center - span : center + span + 1]
    candidate = candles[center]
    highs = [bar.high for bar in window]
    lows = [bar.low for bar in window]
    high = candidate.high == max(highs) and sum(value == candidate.high for value in highs) == 1
    low = candidate.low == min(lows) and sum(value == candidate.low for value in lows) == 1
    if high and not low:
        return PivotConfirmation(center, observed_index, "HIGH", candidate.high)
    if low and not high:
        return PivotConfirmation(center, observed_index, "LOW", candidate.low)
    return None


@dataclass(slots=True)
class AwaitingConfirmation:
    side: Side
    family: str
    source_pool: LiquidityPool
    interaction_index: int
    interaction_extreme: float
    deadline_index: int


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    pivot_span_5m: int = 2
    pivot_span_15m: int = 2
    pivot_span_context: int = 1
    confirmation_window_bars: int = 2
    use_5m_liquidity: bool = True
    use_15m_liquidity: bool = True
    require_reclaim: bool = True
    require_htf_alignment: bool = False
    enable_sweep_ob: bool = True
    enable_break_ob: bool = True
    enable_direct_sweep: bool = False
    enable_direct_break: bool = False
    min_body_ratio: float = 1.0
    max_body_ratio: float | None = None
    min_previous_body_atr: float = 0.0
    max_current_body_atr: float | None = None
    tick_size: float = 0.1


class EasyChartScenarioEngine:
    def __init__(self, symbol: str, config: ScenarioConfig) -> None:
        self.symbol = symbol
        self.config = config
        self.active_pools: dict[str, LiquidityPool] = {}
        self.awaiting: list[AwaitingConfirmation] = []
        self.sequence = 0
        self.used_causal_events: set[str] = set()
        self.diagnostics: dict[str, int] = defaultdict(int)
        self.context_highs: list[float] = []
        self.context_lows: list[float] = []
        self.context_bias = "UNRESOLVED"
        self.true_ranges: list[float] = []

    def _pool_id(self, timeframe: int, pivot: PivotConfirmation, candle: Candle) -> str:
        return f"{self.symbol}:{timeframe}m:{pivot.side}:{candle.ts_open_ns}:{pivot.level:.12g}"

    def add_confirmed_pool(self, timeframe: int, pivot: PivotConfirmation, candles: list[Candle]) -> None:
        center = candles[pivot.center_index]
        observed = candles[pivot.observed_index]
        pool = LiquidityPool(
            pool_id=self._pool_id(timeframe, pivot, center),
            side=pivot.side,
            level=pivot.level,
            event_time_ns=center.ts_close_ns,
            observed_time_ns=observed.ts_close_ns,
            timeframe_minutes=timeframe,
            strength=2 if timeframe >= 15 else 1,
        )
        self.active_pools[pool.pool_id] = pool
        self.diagnostics[f"pools_{timeframe}m"] += 1

    def add_context_pivot(self, pivot: PivotConfirmation) -> None:
        values = self.context_highs if pivot.side == "HIGH" else self.context_lows
        values.append(float(pivot.level)); del values[:-2]
        self._recompute_context_bias(None)

    def update_context_close(self, close: float) -> None:
        self._recompute_context_bias(float(close))

    def _recompute_context_bias(self, close: float | None) -> None:
        if close is not None and self.context_highs and close > self.context_highs[-1]:
            self.context_bias = "BULL"; return
        if close is not None and self.context_lows and close < self.context_lows[-1]:
            self.context_bias = "BEAR"; return
        if len(self.context_highs) >= 2 and len(self.context_lows) >= 2:
            if self.context_highs[-1] > self.context_highs[-2] and self.context_lows[-1] > self.context_lows[-2]:
                self.context_bias = "BULL"; return
            if self.context_highs[-1] < self.context_highs[-2] and self.context_lows[-1] < self.context_lows[-2]:
                self.context_bias = "BEAR"; return
        self.context_bias = "UNRESOLVED"

    def _side_allowed(self, side: Side) -> bool:
        if not self.config.require_htf_alignment:
            return True
        return (side is Side.LONG and self.context_bias == "BULL") or (
            side is Side.SHORT and self.context_bias == "BEAR"
        )

    def _atr(self) -> float | None:
        if len(self.true_ranges) < 14:
            return None
        return sum(self.true_ranges[-14:]) / 14.0

    def _ob_quality(self, ob: EasyChartOrderBlock) -> bool:
        if ob.body_ratio + 1e-12 < self.config.min_body_ratio:
            self.diagnostics["ob_ratio_too_small"] += 1; return False
        if self.config.max_body_ratio is not None and ob.body_ratio > self.config.max_body_ratio + 1e-12:
            self.diagnostics["ob_ratio_too_large"] += 1; return False
        atr = self._atr()
        if atr and self.config.min_previous_body_atr > 0 and ob.previous_body / atr < self.config.min_previous_body_atr:
            self.diagnostics["ob_previous_body_too_small"] += 1; return False
        if atr and self.config.max_current_body_atr is not None and ob.current_body / atr > self.config.max_current_body_atr:
            self.diagnostics["ob_current_body_exhaustive"] += 1; return False
        return True

    def _interactions(self, bar: Candle, previous_close: float) -> list[tuple[LiquidityPool, Side, str, float]]:
        out=[]
        for pool in list(self.active_pools.values()):
            if pool.observed_time_ns >= bar.ts_close_ns:
                continue
            if pool.side == "LOW" and bar.low < pool.level and previous_close >= pool.level:
                if bar.close > pool.level or not self.config.require_reclaim:
                    out.append((pool,Side.LONG,"SWEEP",bar.low))
            elif pool.side == "HIGH" and bar.high > pool.level and previous_close <= pool.level:
                if bar.close < pool.level or not self.config.require_reclaim:
                    out.append((pool,Side.SHORT,"SWEEP",bar.high))
            if pool.side == "HIGH" and previous_close <= pool.level < bar.close:
                out.append((pool,Side.LONG,"BREAK",bar.low))
            elif pool.side == "LOW" and previous_close >= pool.level > bar.close:
                out.append((pool,Side.SHORT,"BREAK",bar.high))
        # A single candle crossing several nested pivots is one liquidity
        # episode, not several independent trades.  Keep one representative
        # per direction/state, preferring higher-timeframe and then the level
        # first encountered from the previous close.
        out.sort(key=lambda x:(-x[0].strength,abs(x[0].level-previous_close),x[0].pool_id,x[2]))
        selected=[]
        seen=set()
        for item in out:
            key=(item[1],item[2])
            if key in seen:
                self.diagnostics[f"nested_{item[2].lower()}_pool_collapsed"]+=1
                continue
            seen.add(key); selected.append(item)
        return selected

    def _opposite_structure(self, source: LiquidityPool, side: Side, current: Candle) -> LiquidityPool | None:
        candidates=[p for p in self.active_pools.values() if p.pool_id!=source.pool_id and p.timeframe_minutes==source.timeframe_minutes]
        candidates=[p for p in candidates if (side is Side.LONG and p.side=="HIGH" and p.level>current.high) or (side is Side.SHORT and p.side=="LOW" and p.level<current.low)]
        if not candidates:
            candidates=[p for p in self.active_pools.values() if p.pool_id!=source.pool_id and ((side is Side.LONG and p.side=="HIGH" and p.level>current.high) or (side is Side.SHORT and p.side=="LOW" and p.level<current.low))]
        # opposite side of the most recently established range, not a distant price-picked target
        candidates.sort(key=lambda p:(p.observed_time_ns,p.strength),reverse=True)
        return candidates[0] if candidates else None

    def on_five_minute_close(self, candles: list[Candle], index: int) -> list[ArmedSetup]:
        if index < 1: return []
        current,previous=candles[index],candles[index-1]
        tr=max(current.high-current.low,abs(current.high-previous.close),abs(current.low-previous.close))
        self.true_ranges.append(tr)
        setups=[]
        ob=detect_easychart_order_block(previous,current)
        ob_valid=ob is not None and self._ob_quality(ob)
        if ob is not None: self.diagnostics["order_blocks"]+=1

        if ob_valid:
            matching=[p for p in self.awaiting if p.side is ob.side and index<=p.deadline_index and self._side_allowed(p.side)]
            matching.sort(key=lambda p:(-p.source_pool.strength,-p.interaction_index,p.source_pool.pool_id))
            for pending in matching:
                setup=self._make_ob_setup(pending,ob,current)
                if setup:
                    setups.append(setup); self.used_causal_events.add(setup.causal_event_id); break
        self.awaiting=[p for p in self.awaiting if index<p.deadline_index]

        for pool,side,kind,extreme in self._interactions(current,previous.close):
            if not self._side_allowed(side):
                self.diagnostics[f"htf_rejected_{kind}_{side.name}"]+=1; continue
            if kind=="SWEEP" and self.config.enable_direct_sweep:
                setup=self._make_direct_sweep(pool,side,extreme,current,index)
                if setup: setups.append(setup); self.used_causal_events.add(setup.causal_event_id)
            if kind=="BREAK" and self.config.enable_direct_break:
                setup=self._make_direct_break(pool,side,extreme,current,index)
                if setup: setups.append(setup); self.used_causal_events.add(setup.causal_event_id)
            ob_enabled=(kind=="SWEEP" and self.config.enable_sweep_ob) or (kind=="BREAK" and self.config.enable_break_ob)
            if ob_enabled:
                family="SWEEP_RECLAIM_OB" if kind=="SWEEP" else "BREAK_ACCEPT_RETEST_OB"
                pending=AwaitingConfirmation(side,family,pool,index,extreme,index+self.config.confirmation_window_bars)
                self.awaiting.append(pending)
                if ob_valid and ob.side is side:
                    setup=self._make_ob_setup(pending,ob,current)
                    if setup: setups.append(setup); self.used_causal_events.add(setup.causal_event_id)

        # crossed levels have served their causal role; setups retain copies
        for pool_id,pool in list(self.active_pools.items()):
            if (pool.side=="HIGH" and current.high>pool.level) or (pool.side=="LOW" and current.low<pool.level):
                self.active_pools.pop(pool_id,None)
        return self._deduplicate(setups)

    def _new_id(self) -> str:
        self.sequence+=1; return f"ec-{self.symbol}-{self.sequence:08d}"

    def _make_ob_setup(self,pending:AwaitingConfirmation,ob:EasyChartOrderBlock,current:Candle)->ArmedSetup|None:
        causal=f"{pending.family}:{pending.source_pool.pool_id}:{pending.interaction_index}"
        if causal in self.used_causal_events: return None
        entry=ob.proximal
        if pending.family=="BREAK_ACCEPT_RETEST_OB":
            entry=max(entry,pending.source_pool.level) if pending.side is Side.LONG else min(entry,pending.source_pool.level)
        if pending.side is Side.LONG:
            stop=min(pending.interaction_extreme,ob.formation_low,pending.source_pool.level)-self.config.tick_size
            target=current.high
        else:
            stop=max(pending.interaction_extreme,ob.formation_high,pending.source_pool.level)+self.config.tick_size
            target=current.low
        return ArmedSetup(
            self._new_id(),causal,self.symbol,pending.family,pending.side,current.ts_close_ns,
            entry,stop,TargetMode.IMPULSE_EXTREME,target,"IMPULSE_EXTREME",pending.source_pool.pool_id,
            ob.zone_low,ob.zone_high,ob.formation_low if pending.side is Side.LONG else ob.formation_high,
            ob.body_ratio,ob.previous_body,ob.current_body,self.context_bias,pending.source_pool.timeframe_minutes,
        )

    def _make_direct_break(self,pool:LiquidityPool,side:Side,extreme:float,current:Candle,index:int)->ArmedSetup|None:
        causal=f"BREAK_ACCEPT_RETEST:{pool.pool_id}:{index}"
        if causal in self.used_causal_events:return None
        entry=pool.level
        stop=(min(pool.level,current.low)-self.config.tick_size) if side is Side.LONG else (max(pool.level,current.high)+self.config.tick_size)
        target=current.high if side is Side.LONG else current.low
        return ArmedSetup(self._new_id(),causal,self.symbol,"BREAK_ACCEPT_RETEST",side,current.ts_close_ns,entry,stop,TargetMode.IMPULSE_EXTREME,target,"IMPULSE_EXTREME",pool.pool_id,pool.level,pool.level,extreme,0.0,0.0,0.0,self.context_bias,pool.timeframe_minutes)

    def _make_direct_sweep(self,pool:LiquidityPool,side:Side,extreme:float,current:Candle,index:int)->ArmedSetup|None:
        causal=f"SWEEP_RECLAIM_RETEST:{pool.pool_id}:{index}"
        if causal in self.used_causal_events:return None
        target_pool=self._opposite_structure(pool,side,current)
        if target_pool is None:
            self.diagnostics["no_opposite_structure"]+=1; return None
        entry=pool.level
        stop=(extreme-self.config.tick_size) if side is Side.LONG else (extreme+self.config.tick_size)
        setup=ArmedSetup(self._new_id(),causal,self.symbol,"SWEEP_RECLAIM_RETEST",side,current.ts_close_ns,entry,stop,TargetMode.FIXED_STRUCTURE,target_pool.level,target_pool.pool_id,pool.pool_id,pool.level,pool.level,extreme,0.0,0.0,0.0,self.context_bias,pool.timeframe_minutes)
        if setup.executable(target_pool.level,target_id=target_pool.pool_id) is None:
            self.diagnostics["fixed_target_rr_lt_1"]+=1; return None
        return setup

    @staticmethod
    def _deduplicate(setups:Iterable[ArmedSetup])->list[ArmedSetup]:
        out={}
        for setup in setups:
            out.setdefault(setup.causal_event_id,setup)
        return sorted(out.values(),key=lambda s:(s.observed_time_ns,s.symbol,s.setup_id))
