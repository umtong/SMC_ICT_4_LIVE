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
from causal_context_control import first_matching_reason, signal_exit_contract
from nautilus_execution import NautilusExecutionMixin
from nautilus_lifecycle import NautilusLifecycleMixin
from nautilus_records import NautilusRecordMixin
from smc_ict_4.contracts import ResearchEvent


def _make_scenario_engine(logic_params: Mapping[str, Any]) -> Any:
    """Instantiate the explicitly requested causal state machine.

    This selector contains no performance logic. Configured experiments choose
    one named market hypothesis, while execution, fills and accounting remain in
    NautilusTrader.
    """
    name = str(logic_params.get("engine", "LIQUIDITY_RESPONSE_BIFURCATION"))
    if name in {"LIQUIDITY_RESPONSE_BIFURCATION", "LRB_POST_SWEEP_RESPONSE"}:
        return LiquidityResponseScenarioEngine(logic_params)
    if name == "SESSION_LIQUIDITY_TRANSFER":
        from session_engine import SessionLiquidityTransferEngine

        return SessionLiquidityTransferEngine(logic_params)
    if name == "SESSION_DISPLACEMENT_RETEST":
        from session_displacement_engine import SessionDisplacementRetestEngine

        return SessionDisplacementRetestEngine(logic_params)
    if name == "SESSION_EQUILIBRIUM_RETEST":
        from session_equilibrium_engine import SessionEquilibriumRetestEngine

        return SessionEquilibriumRetestEngine(logic_params)
    if name == "SESSION_LIQUIDITY_RELAY":
        from session_relay_engine import SessionLiquidityRelayEngine

        return SessionLiquidityRelayEngine(logic_params)
    if name == "ROLLING_AUCTION_LIQUIDITY_RELAY":
        from auction_relay_engine import RollingAuctionLiquidityRelayEngine

        return RollingAuctionLiquidityRelayEngine(logic_params)
    if name == "ROLLING_AUCTION_STRUCTURAL_STOP":
        from auction_structural_stop_engine import RollingAuctionStructuralStopEngine

        return RollingAuctionStructuralStopEngine(logic_params)
    if name == "FIXED_INTERVAL_AUCTION_RELAY":
        from fixed_interval_auction_engine import FixedIntervalAuctionLiquidityRelayEngine

        return FixedIntervalAuctionLiquidityRelayEngine(logic_params)
    if name == "FIXED_INTERVAL_AUCTION_FAILED_TRAP":
        from failed_auction_trap_engine import FailedAuctionTrapRelayEngine

        return FailedAuctionTrapRelayEngine(logic_params)
    if name == "FIVE_MINUTE_DISPLACEMENT_REBALANCE":
        from displacement_rebalance_engine import FiveMinuteDisplacementRebalanceEngine

        return FiveMinuteDisplacementRebalanceEngine(logic_params)
    if name == "ACCEPTED_EXPANSION_PULLBACK":
        from accepted_expansion_engine import AcceptedExpansionPullbackEngine

        return AcceptedExpansionPullbackEngine(logic_params)
    if name == "INVENTORY_ABSORPTION_PULLBACK":
        from inventory_absorption_engine import InventoryAbsorptionPullbackEngine

        return InventoryAbsorptionPullbackEngine(logic_params)
    if name == "HIERARCHICAL_SWEEP_CONTINUATION":
        from hierarchical_sweep_engine import HierarchicalLiquiditySweepContinuationEngine

        return HierarchicalLiquiditySweepContinuationEngine(logic_params)
    if name == "HIERARCHICAL_CONFIRMED_POOL":
        from hierarchical_pool_engine import HierarchicalConfirmedPoolContinuationEngine

        return HierarchicalConfirmedPoolContinuationEngine(logic_params)
    if name == "HIERARCHICAL_FLOW_FACTORIZED":
        from hierarchical_flow_factor_engine import HierarchicalFlowFactorizedEngine

        return HierarchicalFlowFactorizedEngine(logic_params)
    if name == "HIERARCHICAL_MULTI_LIQUIDITY":
        from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine

        return HierarchicalMultiLiquidityEngine(logic_params)
    if name == "ADAPTIVE_FRESH_HIERARCHICAL":
        from adaptive_fresh_hierarchical_engine import AdaptiveFreshHierarchicalEngine

        return AdaptiveFreshHierarchicalEngine(logic_params)
    if name == "SURPRISE_IMPACT_HIERARCHICAL":
        from surprise_impact_hierarchical_engine import SurpriseImpactHierarchicalEngine

        return SurpriseImpactHierarchicalEngine(logic_params)
    if name == "ABSORPTION_CONFIRMED_STRUCTURE_REVERSAL":
        from absorption_structure_engine import AbsorptionConfirmedStructureReversalEngine

        return AbsorptionConfirmedStructureReversalEngine(logic_params)
    if name == "SEQUENTIAL_IMPACT_PERSISTENCE_RELAY":
        from sequential_impact_persistence_engine import SequentialImpactPersistenceRelayEngine

        return SequentialImpactPersistenceRelayEngine(logic_params)
    if name == "UNRESOLVED_OBJECTIVE_LIFECYCLE":
        from objective_lifecycle_engine import UnresolvedObjectiveLifecycleEngine

        return UnresolvedObjectiveLifecycleEngine(logic_params)
    if name == "PERIODIC_OPENING_LIQUIDITY_RELAY":
        from periodic_opening_engine import PeriodicOpeningLiquidityRelayEngine

        return PeriodicOpeningLiquidityRelayEngine(logic_params)
    if name == "MULTI_TIMESCALE_LIQUIDITY_RELAY":
        from composite_engine import MultiTimescaleLiquidityRelayEngine

        return MultiTimescaleLiquidityRelayEngine(logic_params)
    raise ValueError(f"unsupported candidate-06 causal engine: {name}")


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
            self._scenario_engine = _make_scenario_engine(logic_params)
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

        def _apply_causal_context_control(
            self,
            transitions: tuple[Any, ...],
            snapshot: PrimitiveSnapshot,
        ) -> None:
            pending = self._pending_signal
            if pending is not None:
                codes, _ = signal_exit_contract(pending)
                reason = first_matching_reason(transitions, codes)
                if reason is not None:
                    self._pending_signal = None
                    self._pending_created_index = None
                    self._abstain_signal(
                        pending,
                        snapshot,
                        "CAUSAL_CONTEXT_INVALIDATED_BEFORE_ENTRY",
                        {"invalidation_reason": reason},
                    )

            trade = self._active_trade
            if trade is None or self._exit_inflight:
                return
            codes = tuple(trade.get("causal_exit_reason_codes", ()))
            if not bool(trade.get("causal_exit_open_position", False)):
                return
            reason = first_matching_reason(transitions, codes)
            if reason is None:
                return
            trade["forced_exit_reason"] = f"CAUSAL_CONTEXT_INVALIDATION_{reason}"
            trade["causal_context_invalidation_reason"] = reason
            self._exit_inflight = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

        def _observe_scenario_without_new_entry(self, snapshot: PrimitiveSnapshot, ts_ns: int) -> None:
            """Advance clocks and apply only scenario-declared invalidations."""
            step = self._scenario_engine.observe(snapshot, allow_new=False)
            self._record_transitions(step.transitions, ts_ns)
            self._apply_causal_context_control(step.transitions, snapshot)

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
                self._observe_scenario_without_new_entry(snapshot, ts_ns)
                self._manage_open_position(snapshot)
                return

            if self._entry_inflight or self._exit_inflight:
                self._observe_scenario_without_new_entry(snapshot, ts_ns)
                return

            if self._pending_signal is not None:
                self._observe_scenario_without_new_entry(snapshot, ts_ns)
                if self._pending_signal is None:
                    return
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
                self.diagnostics["signals_armed"] += 1
                timing = str(
                    self._logic_params.get(
                        "signal_submission_timing",
                        "NEXT_COMPLETED_BAR",
                    ),
                ).upper()
                timing_counts = self.diagnostics.setdefault(
                    "signal_submission_timing_counts",
                    {},
                )
                timing_counts[timing] = int(timing_counts.get(timing, 0)) + 1
                if timing == "ON_SIGNAL_CLOSE":
                    # The scenario signal is created only after this completed
                    # bar is observable. Nautilus processes a market order
                    # submitted from on_bar against the current bar close; it
                    # cannot use the completed bar's high/low path retroactively.
                    self._attempt_entry(step.signal, snapshot)
                elif timing == "NEXT_COMPLETED_BAR":
                    self._pending_signal = step.signal
                    self._pending_created_index = snapshot.index
                else:
                    raise ValueError(
                        f"unsupported signal_submission_timing: {timing}",
                    )

    return LRBStrategyConfig, LRBStrategy
