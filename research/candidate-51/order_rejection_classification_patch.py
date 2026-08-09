"""Classify one narrow expected order-rejection race.

``actual-fill-v1`` immediately cancels children and submits a reduce-only market
flatten when the next executable fill is outside the frozen scenario or exceeds
the current-NAV 3% planned-loss budget.  With asynchronous contingent orders a
previous child can flatten first; the later redundant reduce-only market order
is then correctly rejected because no position remains.

That event is operationally important but not an alpha or accounting failure.
This patch records it separately only when all of the following are true:

* an actual-fill invalidation was explicitly registered;
* the rejected event is for the same instrument;
* Nautilus reports the portfolio already flat at callback time.

Every other rejection is delegated unchanged to the original strategy handler.
"""
from __future__ import annotations

from typing import Any

import strategy

_original_opened = strategy.Candidate35Strategy.on_position_opened
_original_rejected = strategy.Candidate35Strategy.on_order_rejected
_original_closed = strategy.Candidate35Strategy.on_position_closed


def _on_position_opened(self: Any, event: Any) -> None:
    _original_opened(self, event)
    scenario = self.current_scenario
    if scenario and scenario.get("actual_fill_risk_valid") is False:
        symbol = str(scenario.get("symbol", self.current_symbol or ""))
        self._candidate51_pending_invalid_flatten = {
            "symbol": symbol,
            "instrument_id": str(self.instrument_ids.get(symbol, "")),
            "opened_ts_event": int(getattr(event, "ts_event", self._latest_ts())),
        }


def _event_instrument_text(event: Any) -> str:
    direct = getattr(event, "instrument_id", None)
    if direct is not None:
        return str(direct)
    return str(event)


def _on_order_rejected(self: Any, event: Any) -> None:
    pending = getattr(self, "_candidate51_pending_invalid_flatten", None)
    if pending:
        symbol = str(pending.get("symbol", ""))
        instrument_id = self.instrument_ids.get(symbol)
        text = _event_instrument_text(event)
        same_instrument = (
            instrument_id is not None
            and str(instrument_id) in text
        )
        already_flat = (
            instrument_id is not None
            and self.portfolio.is_flat(instrument_id)
        )
        if same_instrument and already_flat:
            diagnostics = self.diagnostics
            diagnostics["benign_post_invalidation_flatten_rejections"] = int(
                diagnostics.get(
                    "benign_post_invalidation_flatten_rejections",
                    0,
                )
            ) + 1
            self._event(
                "BENIGN_POST_INVALIDATION_FLATTEN_REJECTION",
                int(getattr(event, "ts_event", self._latest_ts())),
                symbol=symbol,
                event=str(event),
                reason=(
                    "REDUNDANT_REDUCE_ONLY_FLATTEN_ARRIVED_AFTER_ACCOUNT_FLAT"
                ),
            )
            self._candidate51_pending_invalid_flatten = None
            return
    _original_rejected(self, event)


def _on_position_closed(self: Any, event: Any) -> None:
    # Preserve the marker until a possible later redundant reduce-only rejection
    # is delivered.  A subsequent valid position open replaces it.
    _original_closed(self, event)


strategy.Candidate35Strategy.on_position_opened = _on_position_opened
strategy.Candidate35Strategy.on_order_rejected = _on_order_rejected
strategy.Candidate35Strategy.on_position_closed = _on_position_closed
