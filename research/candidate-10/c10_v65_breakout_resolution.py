"""Breakout-resolution auction router for Candidate 10 v65.

A completed five-minute flow breakout arms an episode but does not place an
order. A later completed bar must resolve the episode:

* retest and hold of the broken acceptance boundary -> continuation;
* close back through the boundary with opposite aggressor flow -> failed
  acceptance reversal;
* otherwise -> unresolved/no trade at the frozen retrace horizon.

Entry, invalidation and nearest still-live five-minute objective all belong to
the resolved leg. The cross-market event interval begins when the broken pivot
became causally known and ends only at resolution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from c10_v64_intraday_delivery import (
    InternalLiquidityTarget,
    IntradayDeliveryContinuationEngine,
)
from logic import MINUTE_NS, BarObs, Direction, Scenario, TradePlan


@dataclass(slots=True)
class BreakoutEpisode:
    scenario_id: str
    breakout_direction: Direction
    breakout: tuple[int, int, float]
    armed_ts_ns: int
    expire_ts_ns: int
    boundary: float
    zone_low: float
    zone_high: float
    signal_open: float
    signal_high: float
    signal_low: float
    signal_close: float
    signal_signed_flow: float
    signal_relative_volume: float
    extreme_high: float
    extreme_low: float
    boundary_touched: bool = False


class BreakoutResolutionAuctionEngine(IntradayDeliveryContinuationEngine):
    """Stateful continuation/reversal router after causal price discovery."""

    def __init__(self, config: Any, instrument_id: str) -> None:
        super().__init__(config, instrument_id)
        self.breakout_episode: BreakoutEpisode | None = None

    @staticmethod
    def _opposite(direction: Direction) -> Direction:
        return Direction.SHORT if direction == Direction.LONG else Direction.LONG

    def _terminal_episode(
        self,
        episode: BreakoutEpisode,
        bar: BarObs,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._event(
            episode.scenario_id,
            "BREAKOUT_EPISODE_TERMINAL",
            episode.armed_ts_ns,
            bar.ts_ns,
            "OBSERVE",
            "TERMINAL",
            reason,
            episode.boundary,
            details or {},
        )
        self.skips[reason] += 1
        self.breakout_episode = None

    def _arm_episode(
        self,
        *,
        bar: BarObs,
        direction: Direction,
        breakout: tuple[int, int, float],
        relative_volume: float,
    ) -> None:
        key = (direction.value, breakout[0])
        if key in self.used_breakouts:
            self.skips["RESOLUTION_BREAKOUT_ALREADY_USED"] += 1
            return
        zone_low, zone_high = self._zone_from_displacement(
            self.bars,
            self._index,
            direction,
        )
        boundary = (
            max(breakout[2], zone_high)
            if direction == Direction.LONG
            else min(breakout[2], zone_low)
        )
        scenario_id = (
            f"{self.instrument_id}-RESOLUTION-{direction.value}-"
            f"{breakout[0]}-{bar.ts_ns}"
        )
        episode = BreakoutEpisode(
            scenario_id=scenario_id,
            breakout_direction=direction,
            breakout=breakout,
            armed_ts_ns=bar.ts_ns,
            expire_ts_ns=(
                bar.ts_ns + self.config.retrace_expiry_bars * MINUTE_NS
            ),
            boundary=boundary,
            zone_low=zone_low,
            zone_high=zone_high,
            signal_open=bar.open,
            signal_high=bar.high,
            signal_low=bar.low,
            signal_close=bar.close,
            signal_signed_flow=bar.signed_flow,
            signal_relative_volume=relative_volume,
            extreme_high=bar.high,
            extreme_low=bar.low,
        )
        self.used_breakouts.add(key)
        self.breakout_episode = episode
        self._event(
            scenario_id,
            "BREAKOUT_EPISODE_ARMED",
            breakout[0],
            bar.ts_ns,
            "ARMED",
            "OBSERVE",
            "KNOWN_5M_PIVOT_FLOW_BREAKOUT_REQUIRES_LATER_RESOLUTION",
            breakout[2],
            {
                "breakout_direction": direction.value,
                "pivot_known_ts_ns": breakout[1],
                "breakout_level": breakout[2],
                "acceptance_boundary": boundary,
                "zone_low": zone_low,
                "zone_high": zone_high,
                "signal_signed_flow": bar.signed_flow,
                "signal_relative_volume": relative_volume,
                "expire_ts_ns": episode.expire_ts_ns,
            },
        )

    def _build_resolution_plan(
        self,
        *,
        episode: BreakoutEpisode,
        bar: BarObs,
        atr: float,
        resolution: str,
        trade_direction: Direction,
        target: InternalLiquidityTarget,
        context: dict[str, Any],
        relative_volume: float,
    ) -> TradePlan | None:
        entry = episode.boundary
        if trade_direction == Direction.LONG:
            if resolution == "RETEST_CONTINUATION":
                stop_anchor = min(
                    bar.low,
                    episode.zone_low,
                    episode.boundary,
                )
            else:
                stop_anchor = min(
                    episode.extreme_low,
                    bar.low,
                    episode.boundary,
                )
            stop = stop_anchor - self.config.stop_buffer_atr * atr
            reward = target.level - entry
            risk = entry - stop
            passive = entry < bar.close
        else:
            if resolution == "RETEST_CONTINUATION":
                stop_anchor = max(
                    bar.high,
                    episode.zone_high,
                    episode.boundary,
                )
            else:
                stop_anchor = max(
                    episode.extreme_high,
                    bar.high,
                    episode.boundary,
                )
            stop = stop_anchor + self.config.stop_buffer_atr * atr
            reward = entry - target.level
            risk = stop - entry
            passive = entry > bar.close

        if not passive:
            self._terminal_episode(
                episode,
                bar,
                "RESOLUTION_LIMIT_NOT_PASSIVE",
                {"resolution": resolution, "entry": entry, "close": bar.close},
            )
            return None
        if risk <= 0.0 or reward <= 0.0:
            self._terminal_episode(
                episode,
                bar,
                "RESOLUTION_NON_CAUSAL_PRICE_ORDER",
                {
                    "resolution": resolution,
                    "entry": entry,
                    "stop": stop,
                    "target": target.level,
                },
            )
            return None
        if risk / atr < self.config.min_stop_atr:
            self._terminal_episode(
                episode,
                bar,
                "RESOLUTION_STOP_DISTANCE_BELOW_EXECUTION_FLOOR",
                {"resolution": resolution, "risk_atr": risk / atr},
            )
            return None

        maker = self.config.effective_maker_rate
        taker = self.config.effective_taker_rate
        loss = risk + entry * maker + stop * taker
        gain = reward - entry * maker - target.level * maker
        net_r = gain / loss if loss > 0.0 else float("-inf")
        if gain <= 0.0 or net_r < self.config.min_net_r:
            self._terminal_episode(
                episode,
                bar,
                "RESOLUTION_INSUFFICIENT_COSTED_STRUCTURAL_R",
                {
                    "resolution": resolution,
                    "gain": gain,
                    "loss": loss,
                    "net_r": net_r,
                },
            )
            return None

        scenario = (
            Scenario.AAC
            if resolution == "RETEST_CONTINUATION"
            else Scenario.FAR
        )
        resolved_state = (
            "AAC_CONFIRMED"
            if scenario == Scenario.AAC
            else "FAR_CONFIRMED"
        )
        position_expire_ts_ns = bar.ts_ns + self.POSITION_HORIZON_NS
        plan = TradePlan(
            scenario_id=episode.scenario_id,
            scenario=scenario,
            direction=trade_direction,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target.level,
            atr=atr,
            loss_per_unit=loss,
            gain_per_unit=gain,
            net_r=net_r,
            reason_code=(
                "POST_BREAKOUT_RETEST_ACCEPTANCE_CONTINUATION"
                if scenario == Scenario.AAC
                else "POST_BREAKOUT_FAILED_ACCEPTANCE_REVERSAL"
            ),
            expire_ts_ns=(
                bar.ts_ns + self.config.retrace_expiry_bars * MINUTE_NS
            ),
            entry_order_type="LIMIT",
            entry_post_only=True,
            details={
                "sweep_ts_ns": episode.breakout[1],
                "impulse_start_ts_ns": episode.breakout[1],
                "breakout_episode_armed_ts_ns": episode.armed_ts_ns,
                "breakout_direction": episode.breakout_direction.value,
                "resolution": resolution,
                "trade_direction": trade_direction.value,
                "breakout_pivot_event_ts_ns": episode.breakout[0],
                "breakout_pivot_known_ts_ns": episode.breakout[1],
                "breakout_level": episode.breakout[2],
                "acceptance_boundary": episode.boundary,
                "zone_low": episode.zone_low,
                "zone_high": episode.zone_high,
                "signal_open": episode.signal_open,
                "signal_high": episode.signal_high,
                "signal_low": episode.signal_low,
                "signal_close": episode.signal_close,
                "signal_signed_flow": episode.signal_signed_flow,
                "signal_relative_volume": episode.signal_relative_volume,
                "episode_extreme_high": episode.extreme_high,
                "episode_extreme_low": episode.extreme_low,
                "boundary_touched": episode.boundary_touched,
                "resolution_open": bar.open,
                "resolution_high": bar.high,
                "resolution_low": bar.low,
                "resolution_close": bar.close,
                "resolution_signed_flow": bar.signed_flow,
                "resolution_relative_volume": relative_volume,
                "invalidation_anchor": stop_anchor,
                "target_pool_id": target.scenario_id,
                "target_pool_source": target.source,
                "target_pool_event_ts_ns": target.event_ts_ns,
                "target_pool_confirmed_ts_ns": target.confirmed_ts_ns,
                "target_pool_level": target.level,
                "completed_4h_context": context,
                "position_expire_ts_ns": position_expire_ts_ns,
                "position_horizon_minutes": 240,
                "draw_method": (
                    "NEAREST_LIVE_CONFIRMED_5M_INTERNAL_LIQUIDITY"
                ),
                "entry_cost_assumption": "MAKER",
                "entry_expiry_bars": self.config.retrace_expiry_bars,
                "management_contract": (
                    "TARGET_OR_RESOLVED_LEG_INVALIDATION_OR_"
                    "ONE_COMPLETED_4H_HORIZON"
                ),
                "state_sequence": [
                    "COMPLETED_5M_LIQUIDITY",
                    "FLOW_BREAKOUT_EPISODE_ARMED",
                    "LATER_BREAKOUT_RESOLUTION_OBSERVED",
                    resolved_state,
                    "CROSS_MARKET_RESOLUTION_STATE_PENDING",
                    "BROKEN_BOUNDARY_RETEST_ENTRY_PENDING",
                    "INTRADAY_POSITION_OR_ENTRY_TERMINAL",
                ],
                "new_fitted_thresholds": [],
            },
        )
        self.pending_plan_id = episode.scenario_id
        self.pending_target_id = target.scenario_id
        self._event(
            episode.scenario_id,
            "BREAKOUT_EPISODE_RESOLVED",
            episode.armed_ts_ns,
            bar.ts_ns,
            "OBSERVE",
            resolved_state,
            plan.reason_code,
            episode.boundary,
            {
                "resolution": resolution,
                "breakout_direction": episode.breakout_direction.value,
                "trade_direction": trade_direction.value,
                "entry": entry,
                "stop": stop,
                "target": target.level,
                "target_id": target.scenario_id,
                "net_r": net_r,
            },
        )
        self._event(
            episode.scenario_id,
            "TRADE_PLAN_CONFIRMED",
            episode.armed_ts_ns,
            bar.ts_ns,
            resolved_state,
            "PENDING_ENTRY",
            plan.reason_code,
            entry,
            {
                "scenario": scenario.value,
                "direction": trade_direction.value,
                "target": target.level,
                "stop": stop,
                "expire_ts_ns": plan.expire_ts_ns,
                "position_expire_ts_ns": position_expire_ts_ns,
                "net_r": net_r,
            },
        )
        self.breakout_episode = None
        return plan

    def _advance_episode(
        self,
        *,
        bar: BarObs,
        atr: float,
        relative_volume: float,
    ) -> TradePlan | None:
        episode = self.breakout_episode
        assert episode is not None
        if bar.ts_ns > episode.expire_ts_ns:
            self._terminal_episode(
                episode,
                bar,
                "BREAKOUT_RESOLUTION_WINDOW_EXPIRED",
                {
                    "breakout_direction": episode.breakout_direction.value,
                    "boundary_touched": episode.boundary_touched,
                },
            )
            return None

        episode.extreme_high = max(episode.extreme_high, bar.high)
        episode.extreme_low = min(episode.extreme_low, bar.low)
        if episode.breakout_direction == Direction.LONG:
            episode.boundary_touched = (
                episode.boundary_touched or bar.low <= episode.boundary
            )
            failure = (
                bar.close < episode.boundary
                and bar.signed_flow < 0.0
            )
            continuation = (
                episode.boundary_touched
                and bar.close > episode.boundary
                and bar.signed_flow >= 0.0
                and bar.close_location >= 0.50
            )
        else:
            episode.boundary_touched = (
                episode.boundary_touched or bar.high >= episode.boundary
            )
            failure = (
                bar.close > episode.boundary
                and bar.signed_flow > 0.0
            )
            continuation = (
                episode.boundary_touched
                and bar.close < episode.boundary
                and bar.signed_flow <= 0.0
                and bar.close_location <= 0.50
            )

        if failure:
            resolution = "FAILED_ACCEPTANCE_REVERSAL"
            trade_direction = self._opposite(episode.breakout_direction)
        elif continuation:
            resolution = "RETEST_CONTINUATION"
            trade_direction = episode.breakout_direction
        else:
            return None

        target = self._nearest_internal_target(trade_direction, bar)
        if target is None:
            self._terminal_episode(
                episode,
                bar,
                "RESOLUTION_NO_LIVE_INTERNAL_TARGET",
                {
                    "resolution": resolution,
                    "trade_direction": trade_direction.value,
                },
            )
            return None
        context = self._completed_context_state(trade_direction)
        return self._build_resolution_plan(
            episode=episode,
            bar=bar,
            atr=atr,
            resolution=resolution,
            trade_direction=trade_direction,
            target=target,
            context=context,
            relative_volume=relative_volume,
        )

    def _detect_flow_plan(
        self,
        bar: BarObs,
        prev: BarObs,
        atr: float,
        relative_volume: float,
    ) -> TradePlan | None:
        if self.breakout_episode is not None:
            return self._advance_episode(
                bar=bar,
                atr=atr,
                relative_volume=relative_volume,
            )

        high = self._latest_before(self.internal_highs, bar.ts_ns)
        low = self._latest_before(self.internal_lows, bar.ts_ns)
        if high is None or low is None:
            self.skips["RESOLUTION_CAUSAL_INTERNAL_STRUCTURE_UNAVAILABLE"] += 1
            return None
        if (
            bar.body < self.config.reacceleration_body_atr * atr
            or relative_volume < self.config.min_relative_volume
        ):
            return None

        long_breakout = (
            prev.close <= high[2] < bar.close
            and bar.signed_flow >= self.config.reacceleration_flow_min
            and bar.close_location >= 0.60
        )
        short_breakout = (
            prev.close >= low[2] > bar.close
            and bar.signed_flow <= -self.config.reacceleration_flow_min
            and bar.close_location <= 0.40
        )
        if long_breakout and short_breakout:
            self.skips["RESOLUTION_AMBIGUOUS_BOTH_DIRECTIONS"] += 1
            return None
        if not long_breakout and not short_breakout:
            return None

        direction = Direction.LONG if long_breakout else Direction.SHORT
        breakout = high if long_breakout else low
        self._arm_episode(
            bar=bar,
            direction=direction,
            breakout=breakout,
            relative_volume=relative_volume,
        )
        return None


__all__ = ["BreakoutEpisode", "BreakoutResolutionAuctionEngine"]
