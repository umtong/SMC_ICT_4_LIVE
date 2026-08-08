"""Runtime lifecycle correction for Candidate 35 strategy callbacks.

Nautilus passes the close timestamp positionally to ``_event``.  The initial
strategy record also used the key ``ts_event``, which duplicated that argument
when expanding the record.  This patch keeps the close timestamp in evidence as
``closed_ts_event`` and installs the corrected callback before the importable
strategy is instantiated.
"""
from __future__ import annotations

from typing import Any

import strategy


def _on_position_closed(self: Any, event: Any) -> None:
    ts_event = int(getattr(event, "ts_event", self._latest_ts()))
    record = dict(self.current_scenario or {})
    record.update(
        {
            "closed_ts_event": ts_event,
            "realized_pnl": str(getattr(event, "realized_pnl", None)),
            "event": str(event),
        },
    )
    self.closed_scenarios.append(record)
    self._event("POSITION_CLOSED", ts_event, **record)
    self._clear_trade_state()


strategy.Candidate35Strategy.on_position_closed = _on_position_closed
