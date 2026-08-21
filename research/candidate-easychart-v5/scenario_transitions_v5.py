"""Auction-state transitions between interaction and entry for EasyChart v5."""
from __future__ import annotations

from domain import Candle, Side
from contracts_v5 import ScenarioSetup, SetupState, StructureFamily, StructureZone


class ScenarioTransitionMixin:
    def _channel_target_at(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> tuple[StructureZone, float] | None:
        if setup.channel_id is None:
            return None
        channel = self.structure.channel_by_id(setup.channel_id)
        if channel is None:
            return None
        edge = "UPPER" if setup.side is Side.LONG else "LOWER"
        zone = self.structure.channel_edge_snapshot(channel, edge, time_ns)
        price = channel.upper_at(time_ns) if setup.side is Side.LONG else channel.lower_at(time_ns)
        return zone, price
    def _target_is_spent(self, setup: ScenarioSetup, bar: Candle) -> bool:
        dynamic = self._channel_target_at(setup, bar.ts_close_ns)
        if dynamic is not None:
            _, target_price = dynamic
            return (
                bar.high >= target_price
                if setup.side is Side.LONG
                else bar.low <= target_price
            ) and bar.ts_close_ns > setup.interaction_time_ns
        if setup.target_zone is None or setup.target_price is None:
            return True
        touched = bar.high >= setup.target_price if setup.side is Side.LONG else bar.low <= setup.target_price
        if touched and bar.ts_close_ns > setup.interaction_time_ns:
            return True
        return self.structure.target_spent_after(setup.target_zone, setup.interaction_time_ns)
    def _extreme_breached(self, setup: ScenarioSetup, bar: Candle) -> bool:
        return (
            bar.low <= setup.interaction_extreme - self.tick_size
            if setup.side is Side.LONG
            else bar.high >= setup.interaction_extreme + self.tick_size
        )
    def _advance_decision_setups(self, bar: Candle, index: int) -> None:
        for setup in list(self._active.values()):
            if self._target_is_spent(setup, bar):
                self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_entry")
                continue
            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            if setup.state is SetupState.WAITING_RECLAIM:
                setup.interaction_extreme = (
                    min(setup.interaction_extreme, bar.low)
                    if setup.side is Side.LONG
                    else max(setup.interaction_extreme, bar.high)
                )
                reclaimed = bar.close > upper if setup.side is Side.LONG else bar.close < lower
                if reclaimed:
                    setup.confirmation_time_ns = bar.ts_close_ns
                    setup.state = SetupState.WAITING_DISPLACEMENT
                    self._inc("reclaim_confirmed")
                    self._trace("reclaim_confirmed", bar.ts_close_ns, setup)
                continue
            if setup.state is SetupState.WAITING_ACCEPTANCE_HOLD:
                expected = (setup.acceptance_break_index or -1) + 1
                if index != expected:
                    if index > expected:
                        self._finish(
                            setup,
                            SetupState.UNRESOLVED,
                            bar.ts_close_ns,
                            "acceptance_missing_next_decision_bar",
                        )
                    continue
                held = (
                    bar.open > upper and bar.close > upper
                    if setup.side is Side.LONG
                    else bar.open < lower and bar.close < lower
                )
                if not held:
                    self._finish(
                        setup,
                        SetupState.UNRESOLVED,
                        bar.ts_close_ns,
                        "acceptance_failed_next_bar_hold",
                    )
                    continue
                setup.confirmation_time_ns = bar.ts_close_ns
                setup.state = SetupState.WAITING_ACCEPTANCE_RETEST
                self._inc("acceptance_confirmed")
                self._trace("acceptance_confirmed", bar.ts_close_ns, setup)
                continue
            if setup.state in {SetupState.WAITING_DISPLACEMENT, SetupState.WAITING_FOOTPRINT_RETEST}:
                if self._extreme_breached(setup, bar):
                    self._finish(
                        setup,
                        SetupState.INVALIDATED,
                        bar.ts_close_ns,
                        "interaction_extreme_breached_before_entry",
                    )
                    continue
            if setup.state is SetupState.WAITING_ACCEPTANCE_RETEST:
                closed_back_inside = bar.close < lower if setup.side is Side.LONG else bar.close > upper
                if closed_back_inside:
                    self._finish(
                        setup,
                        SetupState.UNRESOLVED,
                        bar.ts_close_ns,
                        "accepted_break_closed_back_inside_before_retest_entry",
                    )
