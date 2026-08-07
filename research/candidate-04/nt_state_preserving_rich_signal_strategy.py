#!/usr/bin/env python3
"""Nautilus execution adapter which preserves the compiler state after fill.

The parent RichSignalStrategy already rejects an actual market fill outside the
causal stop/target bracket.  V56 adds the equally important state contract: the
next-bar fill must still be beyond the structure or liquidity boundary whose
break/reclaim completed the scenario.  A later fill back inside that boundary
means the signal state no longer exists.  The adapter immediately flattens and
never moves the compiler stop or target to fit the later price.
"""
from __future__ import annotations

import math
from typing import Any

from nt_rich_signal_strategy import RichSignalConfig
from nt_rich_signal_strategy import RichSignalStrategy
from nt_rich_signal_strategy import _position_entry_fill


def entry_fill_preserves_state(
    entry_fill: float,
    boundary: float,
    side: int,
) -> bool:
    if side not in (-1, 1):
        return False
    if not all(math.isfinite(value) for value in (entry_fill, boundary)):
        return False
    return side * (entry_fill - boundary) > 0.0


class StatePreservingRichSignalStrategy(RichSignalStrategy):
    """Apply one post-fill causal-state check without changing alpha logic."""

    def _submit_signal(
        self,
        signal: dict[str, Any],
        row: dict[str, float | int],
    ) -> bool:
        submitted = super()._submit_signal(signal, row)
        if not submitted or self.pending_entry_guard is None:
            return submitted
        details = dict(signal.get("details") or {})
        boundary = details.get("actual_fill_state_boundary")
        try:
            value = float(boundary)
        except (TypeError, ValueError):
            return submitted
        if not math.isfinite(value):
            return submitted
        self.pending_entry_guard.update(
            {
                "state_boundary": value,
                "state_boundary_contract": details.get(
                    "actual_fill_state_contract", "compiler_declared_boundary"
                ),
                "state_boundary_rule": details.get(
                    "actual_fill_state_rule",
                    "trade_side_must_remain_beyond_boundary",
                ),
            }
        )
        return submitted

    def on_position_opened(self, event: Any) -> None:
        guard = dict(self.pending_entry_guard or {})
        super().on_position_opened(event)
        boundary = guard.get("state_boundary")
        if boundary is None:
            return
        entry_fill = _position_entry_fill(event)
        side = int(guard.get("side", 0))
        valid = entry_fill_preserves_state(entry_fill, float(boundary), side)
        details = {
            **guard,
            "actual_entry_fill": entry_fill,
            "actual_fill_state_valid": valid,
            "event": str(event),
        }
        if valid:
            self._event(
                "ENTRY_FILL_STATE_VALID",
                str(guard.get("scenario", "UNKNOWN")),
                self.bars[-1],
                details,
            )
            return
        if self.portfolio.is_flat(self.config.instrument_id):
            return
        self._event(
            "ENTRY_FILL_STATE_INVALID",
            str(guard.get("scenario", "UNKNOWN")),
            self.bars[-1],
            details,
        )
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)


__all__ = [
    "RichSignalConfig",
    "StatePreservingRichSignalStrategy",
    "entry_fill_preserves_state",
]
