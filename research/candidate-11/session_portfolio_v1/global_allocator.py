"""Deterministic four-market candidate arbitration for Candidate 11.

This module has no execution or PnL model. It enforces the project invariant that
pending new entries plus open positions across BTC/ETH/SOL/XRP never exceed one.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

ALLOWED_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"})


class SlotState(StrEnum):
    FREE = "FREE"
    ENTRY_PENDING = "ENTRY_PENDING"
    POSITION_OPEN = "POSITION_OPEN"


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    scenario_id: str
    observed_ts_ns: int
    net_structural_r: Decimal
    expected_entry: Decimal
    expected_loss_per_unit: Decimal
    error_bound: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.symbol not in ALLOWED_SYMBOLS:
            raise ValueError(f"unsupported symbol: {self.symbol}")
        if self.observed_ts_ns < 0:
            raise ValueError("observed timestamp must be non-negative")
        if self.net_structural_r <= 0 or self.expected_entry <= 0 or self.expected_loss_per_unit <= 0:
            raise ValueError("candidate geometry must be positive")
        if not Decimal("0") <= self.error_bound <= Decimal("1"):
            raise ValueError("error_bound must be in [0, 1]")

    @property
    def loss_fraction_of_entry(self) -> Decimal:
        return self.expected_loss_per_unit / self.expected_entry


@dataclass(frozen=True, slots=True)
class Arbitration:
    timestamp_ns: int
    winner: Candidate | None
    rejected: tuple[tuple[Candidate, str], ...]


class GlobalCandidateMutex:
    """Single global slot with deterministic same-timestamp arbitration."""

    def __init__(self) -> None:
        self.state = SlotState.FREE
        self.active_scenario_id: str | None = None
        self.active_symbol: str | None = None
        self._timestamp: int | None = None
        self._buffer: list[Candidate] = []

    def _assert_invariant(self) -> None:
        occupied = int(self.state == SlotState.ENTRY_PENDING) + int(self.state == SlotState.POSITION_OPEN)
        if occupied > 1:
            raise AssertionError("global pending-entry plus position invariant violated")
        if self.state == SlotState.FREE and (self.active_scenario_id is not None or self.active_symbol is not None):
            raise AssertionError("free slot cannot retain an active scenario")
        if self.state != SlotState.FREE and (self.active_scenario_id is None or self.active_symbol is None):
            raise AssertionError("occupied slot requires an active scenario and symbol")

    def add(self, candidate: Candidate) -> Arbitration | None:
        """Buffer one timestamp so symbol subscription order cannot select the trade."""
        self._assert_invariant()
        if self._timestamp is None:
            self._timestamp = candidate.observed_ts_ns
        if candidate.observed_ts_ns < self._timestamp:
            raise ValueError("candidates must arrive in non-decreasing timestamp order")
        completed = None
        if candidate.observed_ts_ns > self._timestamp:
            completed = self.flush()
            self._timestamp = candidate.observed_ts_ns
        self._buffer.append(candidate)
        return completed

    @staticmethod
    def _rank(candidate: Candidate) -> tuple[Decimal, Decimal, Decimal, int, str, str]:
        return (
            candidate.error_bound,
            -candidate.net_structural_r,
            candidate.loss_fraction_of_entry,
            candidate.observed_ts_ns,
            candidate.symbol,
            candidate.scenario_id,
        )

    def flush(self) -> Arbitration:
        self._assert_invariant()
        timestamp = self._timestamp if self._timestamp is not None else 0
        candidates = tuple(self._buffer)
        self._buffer.clear()
        if not candidates:
            return Arbitration(timestamp, None, ())
        if self.state != SlotState.FREE:
            return Arbitration(timestamp, None, tuple((item, "GLOBAL_SLOT_OCCUPIED") for item in candidates))
        ordered = sorted(candidates, key=self._rank)
        return Arbitration(
            timestamp,
            ordered[0],
            tuple((item, "LOWER_GLOBAL_PRIORITY") for item in ordered[1:]),
        )

    def mark_entry_submitted(self, candidate: Candidate) -> None:
        self._assert_invariant()
        if self.state != SlotState.FREE:
            raise RuntimeError("global slot is already occupied")
        self.state = SlotState.ENTRY_PENDING
        self.active_scenario_id = candidate.scenario_id
        self.active_symbol = candidate.symbol
        self._assert_invariant()

    def mark_entry_filled(self, scenario_id: str) -> None:
        self._assert_invariant()
        if self.state != SlotState.ENTRY_PENDING or self.active_scenario_id != scenario_id:
            raise RuntimeError("entry fill does not match the active pending scenario")
        self.state = SlotState.POSITION_OPEN
        self._assert_invariant()

    def mark_entry_terminal(self, scenario_id: str) -> None:
        if self.state != SlotState.ENTRY_PENDING or self.active_scenario_id != scenario_id:
            raise RuntimeError("pending-entry terminal event does not match active scenario")
        self._release()

    def mark_position_closed(self, scenario_id: str) -> None:
        if self.state != SlotState.POSITION_OPEN or self.active_scenario_id != scenario_id:
            raise RuntimeError("position close does not match active scenario")
        self._release()

    def _release(self) -> None:
        self.state = SlotState.FREE
        self.active_scenario_id = None
        self.active_symbol = None
        self._assert_invariant()
