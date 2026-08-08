"""Candidate 18 v3: capped IOC entry with explicit partial-fill protection.

The market-state policy is unchanged. Candidate 18 v1 proved that a capped IOC
parent preserves more opportunities than FOK, but the inherited venue setup did
not release proportional OTO children for a partial IOC fill. Candidate 18 v3
reuses the exact IOC strategy and makes the existing NautilusTrader venue
contract explicit: OTO children trigger pro-rata on every partial fill.

The BacktestVenueConfig injection lives in ``candidate.py`` because OTO trigger
mode is a venue/execution setting, not a trading signal. This module exists to
make the effective strategy import unambiguous and to retain v1/v2 code as
separate failure evidence.
"""
from __future__ import annotations

from latency_capped_ioc_strategy import Candidate18Config
from latency_capped_ioc_strategy import Candidate18Strategy as _Candidate18IocStrategy


class Candidate18Strategy(_Candidate18IocStrategy):
    """IOC price cap paired with venue-level proportional OTO protection."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        self.diagnostics["candidate18_partial_oto_ioc_entries"] = 0

    def _submit_entry(self, setup, row):
        submitted = super()._submit_entry(setup, row)
        if submitted:
            self.diagnostics["candidate18_partial_oto_ioc_entries"] = int(
                self.diagnostics["candidate18_partial_oto_ioc_entries"],
            ) + 1
        return submitted


__all__ = ["Candidate18Config", "Candidate18Strategy"]
