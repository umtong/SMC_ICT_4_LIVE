#!/usr/bin/env python3
"""Global one-entry V56 strategy with post-fill state preservation."""
from __future__ import annotations

from typing import Any

from nt_global_rich_signal_strategy import GlobalRichSignalConfig
from nt_global_rich_signal_strategy import GlobalRichSignalStrategy
from nt_rich_signal_strategy import _position_entry_fill
from nt_state_preserving_rich_signal_strategy import entry_fill_preserves_state


class GlobalStatePreservingRichSignalStrategy(GlobalRichSignalStrategy):
    """Combine the existing global coordinator with the frozen V56 fill check."""

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
        self.pending_entry_guard.update(
            {
                "state_boundary": value,
                "state_boundary_contract": details.get(
                    "actual_fill_state_contract", "compiler_declared_boundary"
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
            self._global_event(
                "ENTRY_FILL_STATE_VALID",
                str(guard.get("scenario", "UNKNOWN")),
                self.bars[-1],
                details,
            )
            return
        if self.portfolio.is_flat(self.config.instrument_id):
            return
        self._global_event(
            "ENTRY_FILL_STATE_INVALID",
            str(guard.get("scenario", "UNKNOWN")),
            self.bars[-1],
            details,
        )
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)


__all__ = [
    "GlobalRichSignalConfig",
    "GlobalStatePreservingRichSignalStrategy",
]
