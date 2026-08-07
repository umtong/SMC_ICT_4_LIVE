"""Candidate 05 v43: failed-versus-accepted balance auction competition.

A completed low-efficiency twenty-minute auction defines a temporary dealing
range.  Its first material break is not assumed to continue or reverse.  The
v41 OI/flow/depth state competition chooses failed-auction rejection or
position-building acceptance, and the mature inherited path waits for a causal
retest before submitting any order.
"""
from __future__ import annotations

import math
from typing import Any

from strategy_v41_competing_auction import CompetingAuctionStrategy, CompetingSweep, _finite


class BalanceAuctionCompetitionStrategy(CompetingAuctionStrategy):
    """Compete failure and acceptance at a completed low-efficiency balance."""

    BALANCE_BARS = 20
    BALANCE_MAX_RANGE_ATR = 2.0
    BALANCE_MAX_PATH_EFFICIENCY = 0.35
    BALANCE_BREAK_ATR = 0.10

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.v43_last_signature: tuple[int, int, int] | None = None
        self.diagnostics.update(
            {
                "v43_balances_observed": 0,
                "v43_balance_breaks": 0,
                "v43_two_sided_breaks": 0,
                "v43_duplicate_balances": 0,
            },
        )

    @staticmethod
    def _path_efficiency(rows: list[dict[str, float | int]]) -> float:
        if len(rows) < 2:
            return math.inf
        net = abs(float(rows[-1]["close"]) - float(rows[0]["open"]))
        path = sum(
            abs(float(rows[index]["close"]) - float(rows[index - 1]["close"]))
            for index in range(1, len(rows))
        )
        return net / path if path > 0.0 else 0.0

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        if self.v41_watch is not None:
            self._advance_competing_auction(row)
            return
        if len(self.bars) < self.BALANCE_BARS + 2:
            return
        atr = _finite(self._atr())
        if not math.isfinite(atr) or atr <= 0.0:
            return
        completed = list(self.bars)[-(self.BALANCE_BARS + 1):-1]
        high = max(float(item["high"]) for item in completed)
        low = min(float(item["low"]) for item in completed)
        if (high - low) / atr > self.BALANCE_MAX_RANGE_ATR:
            return
        efficiency = self._path_efficiency(completed)
        if efficiency > self.BALANCE_MAX_PATH_EFFICIENCY:
            return
        signature = (
            int(completed[0]["ts"]),
            round(high / max(atr, 1e-12) * 1_000_000),
            round(low / max(atr, 1e-12) * 1_000_000),
        )
        if signature == self.v43_last_signature:
            self.diagnostics["v43_duplicate_balances"] += 1
            return
        self.diagnostics["v43_balances_observed"] += 1
        broke_high = previous_close <= high and float(row["high"]) >= high + self.BALANCE_BREAK_ATR * atr
        broke_low = previous_close >= low and float(row["low"]) <= low - self.BALANCE_BREAK_ATR * atr
        if broke_high and broke_low:
            self.v43_last_signature = signature
            self.diagnostics["v43_two_sided_breaks"] += 1
            return
        if not broke_high and not broke_low:
            return
        self.v43_last_signature = signature
        direction = 1 if broke_high else -1
        kind = "HIGH" if broke_high else "LOW"
        boundary = high if broke_high else low
        pre = list(self.bars)[-(self.config.structure_lookback_bars + 1):-1]
        if not pre:
            return
        self.scenario_counter += 1
        self.v41_watch = CompetingSweep(
            scenario_id=f"v43-{self.scenario_counter:07d}",
            pool_id=f"balance-{signature[0]}-{kind}",
            pool_kind=kind,
            pool_level=boundary,
            pool_source="COMPLETED_20M_BALANCE",
            pool_strength=1.0,
            pool_created_index=self.bar_index - self.BALANCE_BARS,
            sweep_direction=direction,
            sweep_index=self.bar_index,
            sweep_ts=int(row["ts"]),
            sweep_open=float(row["open"]),
            sweep_close=float(row["close"]),
            sweep_extreme=float(row["high"]) if broke_high else float(row["low"]),
            atr=atr,
            structure_high=max(float(item["high"]) for item in pre),
            structure_low=min(float(item["low"]) for item in pre),
            sweep_oi=self._fv("sum_open_interest"),
        )
        self.diagnostics["v41_sweeps_armed"] += 1
        self.diagnostics["v43_balance_breaks"] += 1


CandidateStrategy = BalanceAuctionCompetitionStrategy
StrategyClass = BalanceAuctionCompetitionStrategy
