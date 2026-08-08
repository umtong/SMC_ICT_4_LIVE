"""Candidate 25: funding-window quarter-hour reset continuation.

This candidate is independent from the failed-auction family.  The inherited
Candidate 19 branches are disabled in configuration; only their validated FOK
execution, risk sizing and NautilusTrader lifecycle are reused.

At 07:45, 15:45 and 23:45 UTC, the first-ten-second imbalance is observed only
after the containing minute closes.  Above-baseline opening participation
creates an event, not an order.  Exactly thirty later completed bars must close
against the original imbalance.  The adverse leg's extreme supplies the stop
geometry and the strategy enters in the original imbalance direction after the
intervening funding settlement.  Candidate 05's funding-flat rule exits before
the next settlement, so the intended 7.5-hour continuation window is captured
without inventing a funding-payment simulator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from funding_window_router import is_funding_window_seed_time
from funding_window_router import reset_confirmed
from funding_window_router import seed_side
from strategy_base import PendingSetup
from transmission_strategy import Candidate19Config
from transmission_strategy import Candidate19Strategy


class Candidate25Config(Candidate19Config, frozen=True):
    quarter_hour_reset_bars: int = 30


@dataclass(slots=True)
class FundingWindowSeed:
    scenario_id: str
    side: int
    created_index: int
    seed_ts: int
    seed_close: float
    reset_low: float
    reset_high: float
    flow_open_10s: float
    opening_participation_burst: float


class Candidate25Strategy(Candidate19Strategy):
    """Trade the post-funding continuation only after a thirty-minute reset."""

    def __init__(self, config: Candidate25Config) -> None:
        super().__init__(config=config)
        if config.enable_rejection or config.enable_acceptance:
            raise ValueError(
                "Candidate 25 is independent; inherited auction branches must be disabled",
            )
        if config.quarter_hour_reset_bars != 30:
            raise ValueError("Candidate 25 uses the pre-registered thirty-bar reset")
        if config.max_hold_bars < 450:
            raise ValueError("max_hold_bars must permit the complete funding window")
        self.funding_seed: FundingWindowSeed | None = None
        self.funding_seed_counter = 0
        self.diagnostics.update(
            {
                "candidate25_funding_seeds": 0,
                "candidate25_resets_observed": 0,
                "candidate25_resets_confirmed": 0,
                "candidate25_resets_rejected": 0,
                "candidate25_resets_skipped_busy": 0,
                "candidate25_fok_entries": 0,
            },
        )

    @staticmethod
    def _utc_clock(ts_ns: int) -> tuple[int, int]:
        stamp = datetime.fromtimestamp(ts_ns / 1_000_000_000.0, tz=timezone.utc)
        return stamp.hour, stamp.minute

    def on_bar(self, bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._advance_funding_seed(row)
        self._maybe_arm_funding_seed(row)

    def _advance_funding_seed(self, row: dict[str, float | int]) -> None:
        seed = self.funding_seed
        if seed is None or self.bar_index <= seed.created_index:
            return

        seed.reset_low = min(seed.reset_low, float(row["low"]))
        seed.reset_high = max(seed.reset_high, float(row["high"]))
        age = self.bar_index - seed.created_index
        if age < self.config.quarter_hour_reset_bars:
            return
        if age > self.config.quarter_hour_reset_bars:
            self._close_seed(row, "THIRTY_BAR_RESET_DECISION_WAS_MISSED")
            return

        self.diagnostics["candidate25_resets_observed"] = int(
            self.diagnostics["candidate25_resets_observed"],
        ) + 1
        if not reset_confirmed(
            side=seed.side,
            seed_close=seed.seed_close,
            reset_close=float(row["close"]),
        ):
            self.diagnostics["candidate25_resets_rejected"] = int(
                self.diagnostics["candidate25_resets_rejected"],
            ) + 1
            self._transition(
                seed.scenario_id,
                "FUNDING_WINDOW_RESET_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "FIRST_THIRTY_MINUTES_DID_NOT_MOVE_AGAINST_SEED_IMBALANCE",
                float(row["close"]),
                self._seed_details(seed, row),
            )
            self.funding_seed = None
            return

        busy = (
            self.pending is not None
            or self.entry_pending
            or not self.portfolio.is_flat(self.config.instrument_id)
            or self.bar_index - self.last_entry_index < self.config.cooldown_bars
        )
        if busy or not self._in_evaluation(int(row["ts"])) or self._funding_blackout(
            int(row["ts"]),
        ):
            self.diagnostics["candidate25_resets_skipped_busy"] = int(
                self.diagnostics["candidate25_resets_skipped_busy"],
            ) + 1
            self._transition(
                seed.scenario_id,
                "FUNDING_WINDOW_RESET_CLOSED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "RESET_CONFIRMED_BUT_GLOBAL_ACCOUNT_OR_TIME_WINDOW_NOT_AVAILABLE",
                float(row["close"]),
                self._seed_details(seed, row),
            )
            self.funding_seed = None
            return

        counter_extreme = seed.reset_low if seed.side > 0 else seed.reset_high
        setup = PendingSetup(
            scenario_id=seed.scenario_id,
            branch="ACCEPTANCE",
            side=seed.side,
            swept_kind="LOW" if seed.side > 0 else "HIGH",
            pool_id=f"funding-window-{seed.seed_ts}",
            pool_level=counter_extreme,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=counter_extreme,
            structure=seed.seed_close,
            atr=self._atr(),
            hold_count=0,
            retrace_armed=True,
            details={
                **self._seed_details(seed, row),
                "candidate25_branch": "POST_FUNDING_RESET_CONTINUATION",
                "countermove_extreme": counter_extreme,
                "entry_clock": "THIRTY_COMPLETED_BARS_AFTER_SEED",
                "time_exit": "INHERITED_PRE_NEXT_FUNDING_FLAT",
            },
        )
        self.pending = setup
        self.diagnostics["candidate25_resets_confirmed"] = int(
            self.diagnostics["candidate25_resets_confirmed"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "FUNDING_WINDOW_RESET_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "THIRTY_MINUTE_COUNTER_MOVE_CREATED_NEW_CONTINUATION_GEOMETRY",
            float(row["close"]),
            setup.details,
        )
        submitted = super()._submit_entry(setup, row)
        if submitted:
            self.diagnostics["candidate25_fok_entries"] = int(
                self.diagnostics["candidate25_fok_entries"],
            ) + 1
        self.funding_seed = None

    def _maybe_arm_funding_seed(self, row: dict[str, float | int]) -> None:
        if self.funding_seed is not None:
            return
        ts = int(row["ts"])
        hour, minute = self._utc_clock(ts)
        if not is_funding_window_seed_time(hour=hour, minute=minute):
            return
        if not self._in_evaluation(ts) or not self._features_ready(ts):
            return
        if self.pending is not None or self.entry_pending:
            return
        if not self.portfolio.is_flat(self.config.instrument_id):
            return

        flow = self._feature("flow_open_10s")
        participation = self._feature("notional_open_10s_burst")
        side = seed_side(
            flow_open_10s=flow,
            opening_participation_burst=participation,
        )
        if side == 0:
            return

        self.funding_seed_counter += 1
        scenario_id = f"qhr-{self.funding_seed_counter:07d}"
        seed = FundingWindowSeed(
            scenario_id=scenario_id,
            side=side,
            created_index=self.bar_index,
            seed_ts=ts,
            seed_close=float(row["close"]),
            reset_low=float(row["low"]),
            reset_high=float(row["high"]),
            flow_open_10s=flow,
            opening_participation_burst=participation,
        )
        self.funding_seed = seed
        self.diagnostics["candidate25_funding_seeds"] = int(
            self.diagnostics["candidate25_funding_seeds"],
        ) + 1
        self._transition(
            scenario_id,
            "FUNDING_WINDOW_SEED_OBSERVED",
            ts,
            ts,
            "THIRTY_MINUTE_RESET_PENDING",
            "ABOVE_BASELINE_FIRST_TEN_SECOND_QUARTER_HOUR_IMBALANCE",
            float(row["close"]),
            self._seed_details(seed, row),
        )

    def _seed_details(
        self,
        seed: FundingWindowSeed,
        row: dict[str, float | int],
    ) -> dict[str, Any]:
        reset_close = float(row["close"])
        signed_reset_bps = (
            seed.side * math.log(reset_close / seed.seed_close) * 10_000.0
            if seed.seed_close > 0.0 and reset_close > 0.0
            else float("nan")
        )
        return {
            "candidate25_branch": "FUNDING_WINDOW_QUARTER_HOUR_SEED",
            "side": seed.side,
            "seed_ts": seed.seed_ts,
            "seed_index": seed.created_index,
            "seed_close": seed.seed_close,
            "flow_open_10s": seed.flow_open_10s,
            "opening_participation_burst": seed.opening_participation_burst,
            "reset_bars": self.bar_index - seed.created_index,
            "reset_close": reset_close,
            "reset_low": seed.reset_low,
            "reset_high": seed.reset_high,
            "signed_reset_bps": signed_reset_bps,
        }

    def _close_seed(self, row: dict[str, float | int], reason: str) -> None:
        seed = self.funding_seed
        if seed is None:
            return
        self._transition(
            seed.scenario_id,
            "FUNDING_WINDOW_SEED_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            self._seed_details(seed, row),
        )
        self.funding_seed = None


__all__ = ["Candidate25Config", "Candidate25Strategy", "FundingWindowSeed"]
