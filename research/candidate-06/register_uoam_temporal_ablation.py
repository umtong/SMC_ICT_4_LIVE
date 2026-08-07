#!/usr/bin/env python3
"""Idempotently add the one-variable UOAM temporal-preexistence ablation."""
from __future__ import annotations

from pathlib import Path

OLD = '''    def _eligible_objective_pools(
        self,
        bar: _AuctionBar,
        bias: _Bias,
    ) -> list[_LiquidityPool]:
        side = "UPPER" if bias.direction == "LONG" else "LOWER"
        pools = [
            pool
            for pool in self._liquidity_pools
            if pool.side == side
            and pool.confirmed_ts_ns < bar.start_ts_ns
            and (
                (pool.level > bar.high)
                if bias.direction == "LONG"
                else (pool.level < bar.low)
            )
        ]
'''
NEW = '''    def _eligible_objective_pools(
        self,
        bar: _AuctionBar,
        bias: _Bias,
    ) -> list[_LiquidityPool]:
        side = "UPPER" if bias.direction == "LONG" else "LOWER"
        timing_mode = str(
            self.params.get(
                "uoam_objective_timing_mode",
                "CONFIRMED_BEFORE_ACCEPTANCE",
            ),
        ).upper()
        if timing_mode == "CONFIRMED_BEFORE_ACCEPTANCE":
            def timing_ok(pool: _LiquidityPool) -> bool:
                return pool.confirmed_ts_ns < bar.start_ts_ns
        elif timing_mode == "SOURCE_BEFORE_CONFIRM_BY_ACCEPTANCE_END":
            def timing_ok(pool: _LiquidityPool) -> bool:
                return (
                    pool.source_ts_ns < bar.start_ts_ns
                    and pool.confirmed_ts_ns <= bar.end_ts_ns
                )
        else:
            raise ValueError(f"unsupported uoam_objective_timing_mode: {timing_mode}")

        pools = [
            pool
            for pool in self._liquidity_pools
            if pool.side == side
            and timing_ok(pool)
            and (
                (pool.level > bar.high)
                if bias.direction == "LONG"
                else (pool.level < bar.low)
            )
        ]
'''


def main() -> int:
    path = Path(__file__).resolve().with_name("objective_lifecycle_engine.py")
    text = path.read_text(encoding="utf-8")
    if "SOURCE_BEFORE_CONFIRM_BY_ACCEPTANCE_END" in text:
        return 0
    if OLD not in text:
        raise RuntimeError("UOAM objective timing anchor changed; refusing ambiguous ablation")
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
