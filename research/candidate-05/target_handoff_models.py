"""State records for Candidate 05 target-liquidity handoff."""
from __future__ import annotations

from dataclasses import dataclass

from logic import Pool


@dataclass(slots=True)
class CurrentLiquidityTarget:
    pool: Pool
    target: float
    target_source: str
    entry_side: int
    source_scenario_id: str


@dataclass(slots=True)
class PendingTargetExit:
    target: CurrentLiquidityTarget
    event_ts: int
    average_exit: float
    realized_pnl: float


@dataclass(slots=True)
class TargetSweepWatch:
    scenario_id: str
    source_scenario_id: str
    pool: Pool
    previous_entry_side: int
    started_index: int
    started_ts: int
    expires_index: int
    atr: float
    open: float
    high: float
    low: float
    sweep_sponsored: bool
    sponsor_directional_flow: float
    sponsor_notional_burst: float
    sponsor_efficiency: float
    rows_observed: int


__all__ = ["CurrentLiquidityTarget", "PendingTargetExit", "TargetSweepWatch"]
