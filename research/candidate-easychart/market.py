"""Causal market-structure extraction and source-faithful scenario generation."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from domain import Candle, EasyChartOrderBlock, LiquidityPool, Side, TradePlan, detect_easychart_order_block, select_structural_target


@dataclass(frozen=True, slots=True)
class PivotConfirmation:
    center_index: int
    observed_index: int
    side: str
    level: float


def confirmed_pivot(candles: list[Candle], observed_index: int, span: int) -> PivotConfirmation | None:
    """Return the pivot whose right-hand confirmation closes at observed_index."""
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
    confirmation_window_bars: int = 2
    use_5m_liquidity: bool = True
    use_15m_liquidity: bool = True
    require_reclaim: bool = True
    enable_sweep_reversal: bool = True
    enable_break_retest: bool = True
    min_body_ratio: float = 1.0
    tick_size: float = 0.1
    min_gross_rr: float = 1.0


class EasyChartScenarioEngine:
    """Generate plans at the first causally observable state transition.

    The engine never sees future pivots.  It separates liquidity interaction
    from the body-engulfing confirmation and creates a plan only after the
    confirming candle closes.  Entry remains the first later revisit.
    """

    def __init__(self, symbol: str, config: ScenarioConfig) -> None:
        self.symbol = symbol
        self.config = config
        self.active_pools: dict[str, LiquidityPool] = {}
        self.awaiting: list[AwaitingConfirmation] = []
        self.sequence = 0
        self.consumed_pool_ids: set[str] = set()
        self.used_causal_events: set[str] = set()
        self.diagnostics: dict[str, int] = defaultdict(int)

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

    def _eligible_interactions(self, bar: Candle, previous_close: float) -> list[tuple[LiquidityPool, Side, str, float]]:
        out: list[tuple[LiquidityPool, Side, str, float]] = []
        for pool in list(self.active_pools.values()):
            if pool.observed_time_ns >= bar.ts_close_ns:
                continue
            if pool.side == "LOW" and bar.low < pool.level and previous_close >= pool.level:
                reclaimed = bar.close > pool.level
                if reclaimed or not self.config.require_reclaim:
                    out.append((pool, Side.LONG, "SWEEP_RECLAIM_OB", bar.low))
            elif pool.side == "HIGH" and bar.high > pool.level and previous_close <= pool.level:
                reclaimed = bar.close < pool.level
                if reclaimed or not self.config.require_reclaim:
                    out.append((pool, Side.SHORT, "SWEEP_RECLAIM_OB", bar.high))
        out.sort(key=lambda item: (-item[0].strength, abs(item[0].level - previous_close), item[0].pool_id))
        return out

    def _break_interactions(self, bar: Candle, previous_close: float) -> list[tuple[LiquidityPool, Side, str, float]]:
        out: list[tuple[LiquidityPool, Side, str, float]] = []
        for pool in list(self.active_pools.values()):
            if pool.observed_time_ns >= bar.ts_close_ns:
                continue
            if pool.side == "HIGH" and previous_close <= pool.level < bar.close:
                out.append((pool, Side.LONG, "BREAK_ACCEPT_RETEST_OB", bar.low))
            elif pool.side == "LOW" and previous_close >= pool.level > bar.close:
                out.append((pool, Side.SHORT, "BREAK_ACCEPT_RETEST_OB", bar.high))
        out.sort(key=lambda item: (-item[0].strength, abs(item[0].level - previous_close), item[0].pool_id))
        return out

    def _consume_crossed_pools(self, bar: Candle) -> None:
        for pool_id, pool in list(self.active_pools.items()):
            crossed = (pool.side == "HIGH" and bar.high > pool.level) or (pool.side == "LOW" and bar.low < pool.level)
            if crossed:
                self.consumed_pool_ids.add(pool_id)
                self.active_pools.pop(pool_id, None)

    def on_five_minute_close(self, candles: list[Candle], index: int) -> list[TradePlan]:
        if index < 1:
            return []
        current = candles[index]
        previous = candles[index - 1]
        plans: list[TradePlan] = []

        # Existing interactions are confirmed by a later/current EC body-engulf.
        ob = detect_easychart_order_block(previous, current)
        if ob is not None:
            self.diagnostics["order_blocks"] += 1
            if ob.body_ratio + 1e-12 >= self.config.min_body_ratio:
                matching = [item for item in self.awaiting if item.side is ob.side and index <= item.deadline_index]
                matching.sort(key=lambda item: (-item.source_pool.strength, -item.interaction_index, item.source_pool.pool_id))
                for pending in matching:
                    plan = self._make_plan(pending, ob, current)
                    if plan is not None:
                        plans.append(plan)
                        self.used_causal_events.add(plan.causal_event_id)
                        self.diagnostics[f"plans_{plan.family}"] += 1
                        break

        self.awaiting = [item for item in self.awaiting if index < item.deadline_index]

        interactions: list[tuple[LiquidityPool, Side, str, float]] = []
        if self.config.enable_sweep_reversal:
            interactions.extend(self._eligible_interactions(current, previous.close))
        if self.config.enable_break_retest:
            interactions.extend(self._break_interactions(current, previous.close))

        for pool, side, family, extreme in interactions:
            self.awaiting.append(
                AwaitingConfirmation(
                    side=side,
                    family=family,
                    source_pool=pool,
                    interaction_index=index,
                    interaction_extreme=extreme,
                    deadline_index=index + self.config.confirmation_window_bars,
                ),
            )
            self.diagnostics[f"interactions_{family}"] += 1

            # Same-bar interaction + body engulf is causally available at close.
            if ob is not None and ob.side is side and ob.body_ratio + 1e-12 >= self.config.min_body_ratio:
                pending = self.awaiting[-1]
                plan = self._make_plan(pending, ob, current)
                if plan is not None:
                    plans.append(plan)
                    self.used_causal_events.add(plan.causal_event_id)
                    self.diagnostics[f"plans_{plan.family}"] += 1

        # Crossed pools cannot be future objectives; interactions already captured.
        self._consume_crossed_pools(current)
        return self._deduplicate(plans)

    def _make_plan(self, pending: AwaitingConfirmation, ob: EasyChartOrderBlock, current: Candle) -> TradePlan | None:
        causal_event_id = f"{pending.family}:{pending.source_pool.pool_id}:{pending.interaction_index}"
        if causal_event_id in self.used_causal_events:
            return None
        entry = ob.proximal
        if pending.family == "BREAK_ACCEPT_RETEST_OB":
            # First retest can occur at either the broken level or the source-defined OB.
            # Choose the first price encountered from the breakout side, not a midpoint.
            if pending.side is Side.LONG:
                entry = max(ob.proximal, pending.source_pool.level)
            else:
                entry = min(ob.proximal, pending.source_pool.level)
        if pending.family == "BREAK_ACCEPT_RETEST_OB":
            if pending.side is Side.LONG:
                stop = min(pending.source_pool.level, ob.formation_low) - self.config.tick_size
            else:
                stop = max(pending.source_pool.level, ob.formation_high) + self.config.tick_size
        elif pending.side is Side.LONG:
            stop = min(pending.interaction_extreme, ob.formation_low) - self.config.tick_size
        else:
            stop = max(pending.interaction_extreme, ob.formation_high) + self.config.tick_size
        pools = [
            pool
            for pool in self.active_pools.values()
            if pool.pool_id != pending.source_pool.pool_id
            and (
                (pending.side is Side.LONG and pool.side == "HIGH" and pool.level > current.high)
                or (pending.side is Side.SHORT and pool.side == "LOW" and pool.level < current.low)
            )
        ]
        target_pool = select_structural_target(
            side=pending.side,
            entry=entry,
            stop=stop,
            pools=pools,
            min_gross_rr=self.config.min_gross_rr,
        )
        if target_pool is None:
            self.diagnostics["no_structural_target"] += 1
            return None
        gross_rr = abs(target_pool.level - entry) / abs(entry - stop)
        self.sequence += 1
        return TradePlan(
            plan_id=f"ec-{self.symbol}-{self.sequence:08d}",
            causal_event_id=causal_event_id,
            symbol=self.symbol,
            family=pending.family,
            side=pending.side,
            observed_time_ns=current.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target_pool.level,
            gross_rr=gross_rr,
            source_pool_id=pending.source_pool.pool_id,
            target_pool_id=target_pool.pool_id,
            zone_low=ob.zone_low,
            zone_high=ob.zone_high,
            formation_extreme=ob.formation_low if pending.side is Side.LONG else ob.formation_high,
            body_ratio=ob.body_ratio,
        )

    @staticmethod
    def _deduplicate(plans: Iterable[TradePlan]) -> list[TradePlan]:
        by_causal: dict[str, TradePlan] = {}
        for plan in plans:
            existing = by_causal.get(plan.causal_event_id)
            if existing is None or plan.gross_rr > existing.gross_rr:
                by_causal[plan.causal_event_id] = plan
        return sorted(by_causal.values(), key=lambda plan: (plan.observed_time_ns, -plan.gross_rr, plan.plan_id))
