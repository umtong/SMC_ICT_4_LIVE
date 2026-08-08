#!/usr/bin/env python3
"""Diagnostic-only idempotency wrapper for v55 research events.

The strategy, orders, fills, targets, risk and account path are unchanged.  The
wrapper records the full payload whenever the exact same ResearchEvent is
appended twice, removes only the second identical evidence record, and lets the
same NautilusTrader run finish so the underlying market result remains visible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from strategy import LiquidityResponseConfig
from strategy_v55_spot_led_price_discovery import SpotLedPriceDiscoveryStrategy


class SpotLedEventIdempotencyDiagnostic(SpotLedPriceDiscoveryStrategy):
    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self._research_event_ids: set[str] = set()
        self._duplicate_research_events: list[dict[str, Any]] = []
        self.diagnostics.update(
            {
                "exact_duplicate_research_events_suppressed": 0,
            },
        )

    def _transition(self, *args: Any, **kwargs: Any) -> None:
        before = len(self.events)
        super()._transition(*args, **kwargs)
        if len(self.events) != before + 1:
            raise RuntimeError("transition did not append exactly one event")
        event = self.events[-1]
        event_id = event.event_id
        if event_id in self._research_event_ids:
            self.events.pop()
            self.diagnostics["exact_duplicate_research_events_suppressed"] += 1
            self._duplicate_research_events.append(
                {
                    "duplicate_index_before_suppression": before,
                    "event": event.to_dict(),
                },
            )
            return
        self._research_event_ids.add(event_id)

    def on_stop(self) -> None:
        destination = Path(self.config.output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "event_duplicate_diagnostics.json").write_text(
            json.dumps(
                self._duplicate_research_events,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        super().on_stop()


LiquidityResponseStrategy = SpotLedEventIdempotencyDiagnostic

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "SpotLedEventIdempotencyDiagnostic",
]
