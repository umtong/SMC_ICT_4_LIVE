"""Candidate-06 NautilusTrader strategy composition."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from logic import (
    BarObservation,
    CausalPrimitiveDetector,
    LiquidityResponseScenarioEngine,
    PrimitiveSnapshot,
    ScenarioSignal,
)
from nautilus_execution import NautilusExecutionMixin
from nautilus_lifecycle import NautilusLifecycleMixin
from nautilus_records import NautilusRecordMixin
from smc_ict_4.contracts import ResearchEvent


def make_strategy_class():
    """Create the strategy lazily so pure logic tests need no Nautilus import."""
    from nautilus_trader.config import StrategyConfig
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Currency
    from nautilus_trader.trading.strategy import Strategy

    class LRBStrategyConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: Any
        starting_balance: Decimal
        risk_fraction: Decimal
        effective_fee_rate: Decimal
        max_holding_bars: int
        final_ts_ns: int
        min_net_rr_after_delay: Decimal
        max_entry_drift_atr: Decimal
        one_tick_slippage_per_fill: bool

    class LRBStrategy(NautilusLifecycleMixin, NautilusExecutionMixin, NautilusRecordMixin, Strategy):
        def __init__(
            self,
            config: LRBStrategyConfig,
            *,
            observations: Mapping[int, BarObservation],
            logic_params: Mapping[str, Any],
        ) -> None:
            super().__init__(config)
            self._observations = dict(observations)
            self._primitive_detector = CausalPrimitiveDetector(logic_params)
            self._scenario_engine = LiquidityResponseScenarioEngine(logic_params)
            self._logic_params = dict(logic_params)
            self._instrument = None
            self._usdt = Currency.from_str("USDT")
            self._bar_index = -1
            self._last_snapshot: PrimitiveSnapshot | None = None
            self._pending_signal: ScenarioSignal | None = None
            self._pending_created_index: int | None = None
            self._entry_inflight = False
            self._exit_inflight = False
            self._active_trade: dict[str, Any] | None = None
            self._scenario_states: dict[str, str] = {}
            self.events: list[ResearchEvent] = []
            self.closed_trades: list[dict[str, Any]] = []
            self.equity_samples: list[dict[str, Any]] = []
            self.errors: list[str] = []
            self.diagnostics: dict[str, Any] = {
                "bars_seen": 0,
                "signals_armed": 0,
                "entries_submitted": 0,
                "entry_abstentions": {},
                "order_denials": 0,
                "order_rejections": 0,
                "equity_query_fallbacks": 0,
            }

        def on_start(self) -> None:
            self._instrument = self.cache.instrument(self.config.instrument_id)
            if self._instrument is None:
                raise RuntimeError(f"instrument not found: {self.config.instrument_id}")
            self.subscribe_bars(self.config.bar_type)

        def on_bar(self, bar: Bar) -> None:
            self.diagnostics["bars_seen"] += 1
            ts_ns = int(bar.ts_event)
            observation = self._observations.get(ts_ns)
            if observation is None:
                raise RuntimeError(f"missing side-channel observation for bar ts_event={ts_ns}")
            snapshot = self._primitive_detector.observe(observation)
            self._last_snapshot = snapshot
            self._bar_index = snapshot.index
            self._sample_equity(ts_ns)

            is_final = ts_ns >= int(self.config.final_ts_ns)
            if is_final:
                self._finalize_at_boundary(snapshot)
                return

            if not self.portfolio.is_flat(self.config.instrument_id):
                self._manage_open_position(snapshot)
                return

            if self._entry_inflight or self._exit_inflight:
                return

            if self._pending_signal is not None:
                if self._pending_created_index is None or snapshot.index <= self._pending_created_index:
                    return
                signal = self._pending_signal
                self._pending_signal = None
                self._pending_created_index = None
                self._attempt_entry(signal, snapshot)
                return

            step = self._scenario_engine.observe(snapshot, allow_new=True)
            self._record_transitions(step.transitions, ts_ns)
            if step.signal is not None:
                self._pending_signal = step.signal
                self._pending_created_index = snapshot.index
                self.diagnostics["signals_armed"] += 1


    return LRBStrategyConfig, LRBStrategy
