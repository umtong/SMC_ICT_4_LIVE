"""Cancellation-aware wrapper for v15 fast diagnostics.

This adds only pending-intent cancellation to the existing non-authoritative
diagnostic simulator.  It does not replace NautilusTrader execution or account
logic.  A cancellation observed at one bar close removes a still-pending setup
before the next bar can fill it; an already-open position is never rewritten.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from domain_v3 import ArmedSetup
from simulator_v3_types import MinuteBar
from simulator_v7 import ExpiringContinuousAccountSimulator


class CancelableExpiringSimulator(ExpiringContinuousAccountSimulator):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cancelled_setup_ids: set[str] = set()

    def cancel_pending(self, setup_ids: Iterable[str], *, reason: str) -> None:
        for setup_id in setup_ids:
            self.cancelled_setup_ids.add(setup_id)
            if setup_id in self.pending:
                setup = self.pending.pop(setup_id).setup
                self.diagnostics["pending_cancelled"] += 1
                self.diagnostics[f"pending_cancelled_{reason}"] += 1
                self.diagnostics[f"pending_cancelled_{setup.family}"] += 1

    def add_setups(self, setups: Iterable[ArmedSetup]) -> None:
        active = []
        for setup in setups:
            if setup.setup_id in self.cancelled_setup_ids:
                self.diagnostics["setup_already_cancelled_before_add"] += 1
                continue
            active.append(setup)
        super().add_setups(active)


__all__ = ["CancelableExpiringSimulator"]
