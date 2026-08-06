"""Failed-absorption acceptance state for candidate-07.

A structural stop on an absorption/reclaim trade is evidence that price was
accepted beyond the swept pool. This module waits for a completed bar close to
hold beyond the stop boundary, then emits one continuation confirmation in the
opposite direction. It contains no order, fill, PnL, or portfolio logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from model import Direction


class AcceptanceOutcome(str, Enum):
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    RECLAIMED = "RECLAIMED"
    TIMED_OUT = "TIMED_OUT"


@dataclass(frozen=True, slots=True)
class AcceptanceObservation:
    outcome: AcceptanceOutcome
    bars_seen: int
    close: float
    reason_code: str


@dataclass(slots=True)
class FailedAbsorptionAcceptance:
    source_scenario_id: str
    direction: Direction
    liquidity_level: float
    acceptance_level: float
    atr: float
    armed_at_ns: int
    timeout_bars: int = 3
    bars_seen: int = 0

    def __post_init__(self) -> None:
        if not self.source_scenario_id:
            raise ValueError("source_scenario_id must not be empty")
        if self.liquidity_level <= 0.0 or self.acceptance_level <= 0.0 or self.atr <= 0.0:
            raise ValueError("price and ATR values must be positive")
        if self.armed_at_ns < 0:
            raise ValueError("armed_at_ns must be non-negative")
        if self.timeout_bars <= 0:
            raise ValueError("timeout_bars must be positive")
        if self.direction is Direction.LONG and self.acceptance_level <= self.liquidity_level:
            raise ValueError("long acceptance must be above liquidity")
        if self.direction is Direction.SHORT and self.acceptance_level >= self.liquidity_level:
            raise ValueError("short acceptance must be below liquidity")

    def observe(self, close: float) -> AcceptanceObservation:
        if close <= 0.0:
            raise ValueError("close must be positive")
        self.bars_seen += 1
        if self.direction is Direction.LONG:
            if close > self.acceptance_level:
                return AcceptanceObservation(
                    AcceptanceOutcome.CONFIRMED,
                    self.bars_seen,
                    close,
                    "FAILED_SHORT_ABSORPTION_ACCEPTED_HIGHER",
                )
            if close <= self.liquidity_level:
                return AcceptanceObservation(
                    AcceptanceOutcome.RECLAIMED,
                    self.bars_seen,
                    close,
                    "FAILED_SHORT_ABSORPTION_RECLAIMED_POOL",
                )
        else:
            if close < self.acceptance_level:
                return AcceptanceObservation(
                    AcceptanceOutcome.CONFIRMED,
                    self.bars_seen,
                    close,
                    "FAILED_LONG_ABSORPTION_ACCEPTED_LOWER",
                )
            if close >= self.liquidity_level:
                return AcceptanceObservation(
                    AcceptanceOutcome.RECLAIMED,
                    self.bars_seen,
                    close,
                    "FAILED_LONG_ABSORPTION_RECLAIMED_POOL",
                )
        if self.bars_seen >= self.timeout_bars:
            return AcceptanceObservation(
                AcceptanceOutcome.TIMED_OUT,
                self.bars_seen,
                close,
                "FAILED_ABSORPTION_ACCEPTANCE_TIMEOUT",
            )
        return AcceptanceObservation(
            AcceptanceOutcome.WAITING,
            self.bars_seen,
            close,
            "FAILED_ABSORPTION_ACCEPTANCE_WAITING",
        )
