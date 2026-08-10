"""Source-fidelity Winner15m execution adapter for Candidate 57.

This preserves the public 15-minute startup requirement and continuous source
signal semantics while retaining the project's mandatory one global account
slot, Nautilus execution, current-NAV 3% planned-loss sizing and realistic
costs.  It deliberately does not impose the earlier six-hour adaptation during
source anatomy.  A separate entry window and run-off window prevent warm-up or
end truncation from masquerading as strategy performance.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from strategy_transition import (
    Candidate35Config as _TransitionConfig,
    Candidate35Strategy as _TransitionStrategy,
)
from strategy_base import SYMBOLS


class Candidate35Config(_TransitionConfig, frozen=True):
    winner_source_startup_candles: int = 200
    winner_entry_start_ns: int = 0
    winner_entry_end_ns: int = 2**63 - 1
    winner_force_flat_ns: int = 2**63 - 1
    winner_bar_buffer_minutes: int = 50_000


class Candidate35Strategy(_TransitionStrategy):
    """One-slot project adapter with faithful source signal availability."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        if int(config.winner_source_startup_candles) != 200:
            raise ValueError("public Winner15m startup_candle_count must remain 200")
        minimum_buffer = (
            int(config.winner_source_startup_candles)
            * int(config.winner_bucket_minutes)
            + 60
        )
        if int(config.winner_bar_buffer_minutes) < minimum_buffer:
            raise ValueError(
                "winner_bar_buffer_minutes is too small for the public startup window"
            )
        # The reused shell retains only 2,000 one-minute bars, which cannot
        # reproduce the source's 200 x 15-minute startup.  Replace only this
        # storage bound; matching, account and order handling stay in Nautilus.
        self.bars = {
            symbol: deque(maxlen=int(config.winner_bar_buffer_minutes))
            for symbol in SYMBOLS
        }
        for key in (
            "source_true_decisions",
            "source_persistent_decisions",
            "source_fresh_decisions",
            "source_forced_flatten_requests",
        ):
            self.diagnostics.setdefault(key, 0)

    def _required_minute_bars(self) -> int:
        source_minutes = (
            int(self.config.winner_source_startup_candles)
            * int(self.config.winner_bucket_minutes)
        )
        return max(source_minutes, super()._required_minute_bars())

    def _decision_boundary(self, moment: datetime) -> bool:
        moment_ns = int(moment.timestamp() * 1_000_000_000)
        if not (
            int(self.config.winner_entry_start_ns)
            <= moment_ns
            <= int(self.config.winner_entry_end_ns)
        ):
            return False
        return super()._decision_boundary(moment)

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        # Request flattening before the final data minute so the market order
        # can receive a subsequent bar and the evaluated account ends flat.
        if ts_event >= int(self.config.winner_force_flat_ns):
            open_symbols = [
                symbol
                for symbol in SYMBOLS
                if not self.portfolio.is_flat(self.instrument_ids[symbol])
            ]
            if self.entry_pending and self.current_symbol is not None:
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self._clear_trade_state()
            for symbol in open_symbols:
                instrument_id = self.instrument_ids[symbol]
                self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.diagnostics["source_forced_flatten_requests"] += 1
                self._event(
                    "WINNER_SOURCE_FIDELITY_RUNOFF_FLATTEN",
                    ts_event,
                    symbol=symbol,
                )
            self._record_equity(ts_event)
            return
        super()._on_complete_universe_minute(ts_event)

    def _submit_decision(self, decision, ts_event: int) -> None:  # type: ignore[override]
        self.diagnostics["source_true_decisions"] += 1
        persistent = int(
            (decision.diagnostics or {}).get("persistent_source_condition", 0)
        )
        key = "source_persistent_decisions" if persistent else "source_fresh_decisions"
        self.diagnostics[key] += 1
        super()._submit_decision(decision, ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
