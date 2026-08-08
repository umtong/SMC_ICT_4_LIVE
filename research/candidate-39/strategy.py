"""Candidate 39 NautilusTrader strategy adapter.

The execution, account, fee, latency, contingent-order, and continuous-NAV
machinery is deliberately reused from Candidate 35. Candidate 39 replaces only
the causal state router and adds execution-integrity corrections learned from
Candidate 16 and the first Candidate 39 replay: rejected/already-crossed
protective stops flatten immediately, and position-close evidence is persisted
without duplicating the event timestamp argument.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "candidate-35"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Candidate 35 imports a module named ``router``. Pin that name to Candidate
# 39 before loading the reused strategy module.
import router as _candidate39_router

sys.modules["router"] = _candidate39_router
_spec = importlib.util.spec_from_file_location(
    "_candidate39_reused_candidate35_strategy",
    BASE / "strategy.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load reused strategy shell from {BASE / 'strategy.py'}")
_base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base
_spec.loader.exec_module(_base)

Candidate39Config = _base.Candidate35Config
Candidate35Config = Candidate39Config  # required by the reused BacktestNode runner


class Candidate39Strategy(_base.Candidate35Strategy):
    """Candidate 35 execution shell with Candidate 39 state and fail-safe policy."""

    def _submit_decision(self, decision: Any, ts_event: int) -> None:
        before = int(self.diagnostics.get("entry_submissions", 0))
        super()._submit_decision(decision, ts_event)
        after = int(self.diagnostics.get("entry_submissions", 0))
        if after > before and self.current_scenario is not None:
            self.current_scenario["scenario_id"] = f"c39-{after:07d}"
            self.current_scenario["candidate"] = "candidate-39-causal-auction-state-router"
            self.current_scenario["non_scalping"] = True
            self.current_scenario["signal_horizon_minutes"] = 15
            self.current_scenario["maximum_hold_minutes"] = int(self.config.max_hold_minutes)

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        scenario = self.current_scenario
        symbol = self.current_symbol
        if not scenario or symbol is None or not self.bars[symbol]:
            return
        side = int(scenario.get("side", 0))
        stop = float(scenario.get("stop", float("nan")))
        latest = self.bars[symbol][-1]
        crossed = (side > 0 and latest.low <= stop) or (side < 0 and latest.high >= stop)
        if not crossed:
            return
        instrument_id = self.instrument_ids[symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self.diagnostics["emergency_flattens"] = int(
            self.diagnostics.get("emergency_flattens", 0)
        ) + 1
        self._event(
            "EMERGENCY_FLATTEN",
            int(getattr(event, "ts_event", self._latest_ts())),
            symbol=symbol,
            reason="PROTECTIVE_STOP_ALREADY_CROSSED_ON_ENTRY_BAR",
            stop=stop,
            bar_high=float(latest.high),
            bar_low=float(latest.low),
        )

    def on_position_closed(self, event: Any) -> None:
        """Persist one close event without re-passing ``ts_event`` as a kwarg.

        Candidate 35 stored ``ts_event`` in the scenario record and then passed
        that record to ``_event`` after also supplying the positional timestamp.
        The first real Candidate 39 stop fill exposed the resulting TypeError.
        This override preserves the evidence schema while making the event call
        unambiguous.
        """
        ts_event = int(getattr(event, "ts_event", self._latest_ts()))
        record = dict(self.current_scenario or {})
        record.update(
            {
                "ts_event": ts_event,
                "realized_pnl": str(getattr(event, "realized_pnl", None)),
                "event": str(event),
            }
        )
        self.closed_scenarios.append(record)
        event_details = dict(record)
        event_details.pop("ts_event", None)
        self._event("POSITION_CLOSED", ts_event, **event_details)
        self._clear_trade_state()

    def on_order_rejected(self, event: Any) -> None:
        super().on_order_rejected(event)
        if self._global_flat():
            return
        # Any rejection while risk is live may be a protective-child rejection.
        # Under a single-position account, the only safe deterministic response
        # is to cancel the residual bracket and flatten immediately.
        symbol = self.current_symbol
        if symbol is None:
            for candidate in _base.SYMBOLS:
                if not self.portfolio.is_flat(self.instrument_ids[candidate]):
                    symbol = candidate
                    break
        if symbol is None:
            return
        instrument_id = self.instrument_ids[symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self.diagnostics["emergency_flattens"] = int(
            self.diagnostics.get("emergency_flattens", 0)
        ) + 1
        self._event(
            "EMERGENCY_FLATTEN",
            int(getattr(event, "ts_event", self._latest_ts())),
            symbol=symbol,
            reason="ORDER_REJECTION_WHILE_POSITION_LIVE",
            rejected_event=str(event),
        )


# The reused runner imports these exact names.
Candidate35Strategy = Candidate39Strategy
