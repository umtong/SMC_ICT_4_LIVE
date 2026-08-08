#!/usr/bin/env python3
"""Conservatively exit when a protective stop becomes marketable before activation.

NautilusTrader can reject a contingent STOP_MARKET child after the parent market entry
fills if the next bar has already crossed the trigger. A live system cannot leave that
position unprotected. This patch changes no signal, threshold, price, risk, cost or date:
it immediately cancels siblings and submits a market close, treating the subsequent
contingent-sibling rejection as an expected OCO consequence rather than alpha.
"""
from pathlib import Path

path = Path(__file__).resolve().parent / "source" / "strategy_adapter.py"
text = path.read_text(encoding="utf-8")
if "PROTECTIVE_STOP_IN_MARKET_EMERGENCY_EXIT" in text:
    raise SystemExit(0)

old = '''        def on_order_rejected(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            record = {"type": "ORDER_REJECTED", "event": str(event)}
            self.errors.append(record)
            if self.active_plan is not None:
                self.logic.mark_plan_rejected(
                    self.active_plan,
                    int(event.ts_event),
                    "ORDER_REJECTED",
                    record,
                )
                self.active_plan = None
'''
new = '''        def on_order_rejected(self, event: Any) -> None:
            message = str(event)
            if "order stop px" in message and "was in the market" in message:
                self._record_order_event(
                    event,
                    "PROTECTIVE_STOP_IN_MARKET_EMERGENCY_EXIT",
                )
                self.cancel_all_orders(self.config.instrument_id)
                if not self.portfolio.is_flat(self.config.instrument_id):
                    self.close_all_positions(self.config.instrument_id)
                if self.active_plan is not None:
                    self.logic.mark_trade_terminal(
                        self.active_plan,
                        int(event.ts_event),
                        "PROTECTIVE_STOP_IN_MARKET_EMERGENCY_EXIT",
                        {"rejected_event": message},
                    )
                return
            if "Contingent order" in message and "already closed" in message:
                self._record_order_event(
                    event,
                    "EXPECTED_CONTINGENT_SIBLING_REJECTION",
                )
                return
            self._record_order_event(event, "ORDER_REJECTED")
            record = {"type": "ORDER_REJECTED", "event": message}
            self.errors.append(record)
            if self.active_plan is not None:
                self.logic.mark_plan_rejected(
                    self.active_plan,
                    int(event.ts_event),
                    "ORDER_REJECTED",
                    record,
                )
                self.active_plan = None
'''
if old not in text:
    raise RuntimeError("Candidate 12 rejection handler contract not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
