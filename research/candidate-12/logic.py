"""Candidate 12 causal liquidity acceptance/rejection state machine.

No matching, fill, fee, position, or NAV loop exists here. NautilusTrader is
the sole execution/accounting authority.
"""
from __future__ import annotations

from collections import Counter, deque
from typing import Any

from confirmation import ScenarioConfirmationMixin
from ledger import LiquidityLedgerMixin
from model import (
    BarObs, ConfirmationState, Direction, LiquidityPool, LogicConfig, ProbeState,
    RiskSizer, ScenarioKind, Side, SizeDecision, TradePlan, _AggBar,
)
from probe import ProbeClassificationMixin


class CausalLiquidityAuctionEngine(
    ScenarioConfirmationMixin, ProbeClassificationMixin, LiquidityLedgerMixin,
):
        def __init__(self, config: LogicConfig, instrument_id: str) -> None:
            self.config = config
            self.instrument_id = instrument_id
            self.events: list[ResearchEvent] = []
            self.skips: Counter[str] = Counter()
            self.scenario_counts: Counter[str] = Counter()
            self.pool_counts: Counter[str] = Counter()
            self._states: dict[str, str] = {}
            self._bars: Deque[BarObs] = deque(maxlen=max(2_000, config.range_tf_minutes + 32))
            self._true_ranges: Deque[float] = deque(maxlen=config.atr_period)
            self._volumes: Deque[float] = deque(maxlen=config.volume_period)
            self._abs_flows: Deque[float] = deque(maxlen=config.flow_period)
            self._internal_window: Deque[BarObs] = deque(maxlen=2 * config.internal_pivot_wing + 1)
            self._external_bars: Deque[_AggBar] = deque(maxlen=2 * config.external_pivot_wing + 1)
            self._pools: list[LiquidityPool] = []
            self._latest_internal_high: tuple[float, int] | None = None
            self._latest_internal_low: tuple[float, int] | None = None
            self._probe: ProbeState | None = None
            self._confirmation: ConfirmationState | None = None
            self._bar_index = -1
            self._previous_close: float | None = None
            self._scenario_counter = 0
            self._last_external_bucket: int | None = None
            self._last_range_bucket: int | None = None
            self._last_day_bucket: int | None = None

        @property
        def pools(self) -> tuple[LiquidityPool, ...]:
            return tuple(pool for pool in self._pools if pool.active)

        def on_bar(self, bar: BarObs, *, allow_entry: bool = True) -> TradePlan | None:
            self._bar_index += 1
            if self._previous_close is not None:
                true_range = max(
                    bar.high - bar.low,
                    abs(bar.high - self._previous_close),
                    abs(bar.low - self._previous_close),
                )
            else:
                true_range = bar.high - bar.low
            self._true_ranges.append(true_range)
            atr = self._atr()
            relative_volume = self._relative_volume(bar.volume)
            # Baselines use observations strictly prior to this completed bar.
            self._bars.append(bar)
            self._update_internal_pivots(bar)
            if atr is not None and atr > 0:
                self._update_external_structures(bar, atr)
                self._expire_pools(bar.ts_ns)

                plan = self._advance_confirmation(bar, atr, allow_entry)
                if plan is not None:
                    self._volumes.append(bar.volume)
                    self._abs_flows.append(abs(bar.signed_flow))
                    self._previous_close = bar.close
                    return plan

                if self._confirmation is None:
                    self._advance_probe(bar, atr, relative_volume)

                if self._probe is None and self._confirmation is None:
                    pool = self._eligible_crossed_pool(bar, atr)
                    if pool is not None:
                        if relative_volume >= self.config.min_probe_relative_volume:
                            self._start_probe(pool, bar, relative_volume)
                        else:
                            self.skips["LOW_ACTIVITY_POOL_CROSS"] += 1

            self._volumes.append(bar.volume)
            self._abs_flows.append(abs(bar.signed_flow))
            self._previous_close = bar.close
            return None

__all__ = [
    "BarObs", "CausalLiquidityAuctionEngine", "ConfirmationState", "Direction",
    "LiquidityPool", "LogicConfig", "ProbeState", "RiskSizer", "ScenarioKind",
    "Side", "SizeDecision", "TradePlan",
]
