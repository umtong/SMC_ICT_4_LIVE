"""Named-regional-auction spot/perpetual/L1 state router.

Liquidity selection, latent-state evidence, transition confirmation and entry
are separate causal roles.  Orders, fills, fees, margin and NAV remain entirely
owned by NautilusTrader through the reused Candidate 05/16 stack.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo

from strategy_base import PendingSetup
from spot_perp_strategy import SpotPerpSessionConfig
from spot_perp_strategy import SpotPerpSessionStrategy

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class RegionalSpec:
    label: str
    range_start: int
    range_end: int
    decision_start: int
    decision_end: int
    strength: int


SPECS = (
    RegionalSpec("US_LATE_1600_2000_NY", 16 * 60, 20 * 60, 20 * 60, 24 * 60, 3),
    RegionalSpec("ASIA_2000_0000_NY", 20 * 60, 0, 2 * 60, 5 * 60, 3),
    RegionalSpec("LONDON_PREMARKET_0000_0200_NY", 0, 2 * 60, 2 * 60, 5 * 60, 2),
    RegionalSpec("LONDON_0200_0500_NY", 2 * 60, 5 * 60, 7 * 60, 10 * 60, 3),
    RegionalSpec("NY_PREMARKET_0500_0700_NY", 5 * 60, 7 * 60, 7 * 60, 10 * 60, 2),
    RegionalSpec("NYAM_0700_1000_NY", 7 * 60, 10 * 60, 10 * 60, 12 * 60, 2),
    RegionalSpec("LONDON_CLOSE_1000_1200_NY", 10 * 60, 12 * 60, 13 * 60, 16 * 60, 2),
)


class RegionalSpotPerpConfig(SpotPerpSessionConfig, frozen=True):
    pass


class RegionalSpotPerpStrategy(SpotPerpSessionStrategy):
    """Route regional interactions to continuation, reversal, or no trade."""

    def __init__(self, config: RegionalSpotPerpConfig) -> None:
        super().__init__(config=config)
        self._regional_ranges: dict[tuple[str, date], dict[str, float | int]] = {}
        self._regional_windows: dict[str, tuple[int, int]] = {}
        self.diagnostics.update(
            {
                "regional_ranges_completed": 0,
                "regional_parent_interactions": 0,
                "regional_out_of_window_pools": 0,
                "regional_broad_attacks": 0,
                "regional_perp_only_attacks": 0,
                "regional_vacuum_persistence": 0,
                "regional_vacuum_rejections": 0,
                "regional_vacuum_entries": 0,
            },
        )

    @staticmethod
    def _local(ts_ns: int) -> datetime:
        return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).astimezone(NEW_YORK)

    @staticmethod
    def _minute_ns(day: date, minute: int) -> int:
        if minute >= 24 * 60:
            day += timedelta(days=1)
            minute -= 24 * 60
        moment = datetime(
            day.year,
            day.month,
            day.day,
            minute // 60,
            minute % 60,
            tzinfo=NEW_YORK,
        )
        return int(moment.astimezone(timezone.utc).timestamp() * 1_000_000_000)

    def _finish_regional_range(self, spec: RegionalSpec, key: date, observed_ns: int) -> None:
        structural = self._regional_ranges.pop((spec.label, key), None)
        if structural is None:
            return
        source = f"SESSION_4H:REGIONAL:{spec.label}:{key.isoformat()}"
        existing = set(self.active_pools)
        self._add_pool(
            "HIGH",
            float(structural["high"]),
            int(structural["high_ts"]),
            observed_ns,
            source,
            strength=spec.strength,
        )
        self._add_pool(
            "LOW",
            float(structural["low"]),
            int(structural["low_ts"]),
            observed_ns,
            source,
            strength=spec.strength,
        )
        decision_day = key + timedelta(days=1) if spec.range_start > spec.range_end else key
        start_ns = self._minute_ns(decision_day, spec.decision_start)
        end_ns = self._minute_ns(decision_day, spec.decision_end)
        for pool_id in set(self.active_pools) - existing:
            self._regional_windows[pool_id] = (start_ns, end_ns)
        self.diagnostics["regional_ranges_completed"] += 1

    def _update_regional_range(self, row: dict[str, float | int], spec: RegionalSpec) -> None:
        local = self._local(int(row["ts"]))
        minute = local.hour * 60 + local.minute
        crosses_midnight = spec.range_start > spec.range_end
        if crosses_midnight:
            inside = minute > spec.range_start or minute <= spec.range_end
            key = local.date() if minute > spec.range_start else local.date() - timedelta(days=1)
        else:
            inside = spec.range_start < minute <= spec.range_end
            key = local.date()
        if not inside:
            return
        session_key = (spec.label, key)
        current = self._regional_ranges.get(session_key)
        high = float(row["high"])
        low = float(row["low"])
        if current is None:
            current = {
                "high": high,
                "low": low,
                "high_ts": int(row["ts"]),
                "low_ts": int(row["ts"]),
            }
            self._regional_ranges[session_key] = current
        else:
            if high > float(current["high"]):
                current["high"] = high
                current["high_ts"] = int(row["ts"])
            if low < float(current["low"]):
                current["low"] = low
                current["low_ts"] = int(row["ts"])
        if minute == spec.range_end:
            self._finish_regional_range(spec, key, int(row["ts"]))

    def _roll_session(self, row: dict[str, float | int]) -> None:
        # Replace anonymous rolling 4h ranges with participant-linked regional auctions.
        for spec in SPECS:
            self._update_regional_range(row, spec)

    def _prune_pools(self, row: dict[str, float | int]) -> None:
        before = set(self.active_pools)
        super()._prune_pools(row)
        for pool_id in before - set(self.active_pools):
            self._regional_windows.pop(pool_id, None)

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        ts_ns = int(row["ts"])
        original = self.active_pools
        eligible = {
            pool_id: pool
            for pool_id, pool in original.items()
            if pool_id in self._regional_windows
            and self._regional_windows[pool_id][0] < ts_ns <= self._regional_windows[pool_id][1]
        }
        self.diagnostics["regional_out_of_window_pools"] += len(original) - len(eligible)
        self.active_pools = dict(eligible)
        try:
            before = self.parent_auction
            super()._detect_sweep(row, previous_close)
            remaining = self.active_pools
            consumed = set(eligible) - set(remaining)
        finally:
            rebuilt = {
                pool_id: pool
                for pool_id, pool in original.items()
                if pool_id not in locals().get("consumed", set())
            }
            rebuilt.update(locals().get("remaining", {}))
            self.active_pools = rebuilt
        if before is None and self.parent_auction is not None and self.pending is not None:
            self.diagnostics["regional_parent_interactions"] += 1
            broad = bool(self.pending.details.get("spot_perp_broad_attack", False))
            perp_only = bool(self.pending.details.get("spot_perp_perp_only_attack", False))
            self.diagnostics["regional_broad_attacks"] += int(broad)
            self.diagnostics["regional_perp_only_attacks"] += int(perp_only)

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.parent_auction
        if setup is None or setup.branch != "OBSERVATION" or state is None:
            return super()._process_pending(row)
        if self.bar_index <= setup.created_index:
            return True

        broad_parent = bool(setup.details.get("spot_perp_broad_attack", False))
        if not broad_parent:
            # Perpetual-only attacks continue through the slower failed-auction
            # path: effort/result failure -> spot rejection -> L1 flip -> initiative.
            return super()._process_pending(row)

        direction = int(state.direction)
        if direction > 0:
            setup.sweep_extreme = max(setup.sweep_extreme, float(row["high"]))
        else:
            setup.sweep_extreme = min(setup.sweep_extreme, float(row["low"]))
        self._accumulate_displayed_state(setup, direction)

        close = float(row["close"])
        outside = direction * (close - setup.pool_level) > 0.0
        perp_persists = (
            direction * self._finite_feature("flow_60s") > 0.0
            and direction * self._finite_feature("ret_60s_bps") > 0.0
        )
        spot_persists = (
            direction * self._finite_feature("spot_flow_60s") > 0.0
            and direction * self._finite_feature("spot_ret_60s_bps") > 0.0
        )
        latest_l1 = setup.details.get("latest_l1_pressure") or {}
        l1_persists = bool(latest_l1.get("parent_pressure_persistence", False))
        transition = {
            "strictly_later_bar": True,
            "outside_regional_boundary": outside,
            "perpetual_flow_and_price_persist": perp_persists,
            "spot_flow_and_price_persist": spot_persists,
            "l1_pressure_persists": l1_persists,
        }
        if not (outside and perp_persists and spot_persists and l1_persists):
            self.diagnostics["regional_vacuum_rejections"] += 1
            self.diagnostics["candidate16_unresolved"] += 1
            self._transition(
                setup.scenario_id,
                "REGIONAL_STATE_ROUTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "BROAD_PARENT_ATTACK_DID_NOT_PERSIST_ON_FIRST_LATER_MINUTE",
                close,
                {**setup.details, "regional_vacuum_transition": transition},
            )
            self.pending = None
            self.parent_auction = None
            return True

        self.diagnostics["regional_vacuum_persistence"] += 1
        self.diagnostics["candidate16_acceptance_continuations"] += 1
        accepted = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="ACCEPTANCE",
            side=direction,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.pool_level,
            atr=setup.atr,
            hold_count=1,
            retrace_armed=False,
            details={
                **setup.details,
                "regional_vacuum_transition": transition,
                "candidate11_branch": "REGIONAL_SPOT_PERP_L1_VACUUM",
            },
        )
        self.pending = accepted
        self.parent_auction = None
        self._transition(
            accepted.scenario_id,
            "REGIONAL_STATE_ROUTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "BROAD_PRICE_DISCOVERY_PERSISTED_IN_HANDOFF_WINDOW",
            close,
            accepted.details,
        )
        submitted = self._submit_entry(accepted, row)
        self.diagnostics["regional_vacuum_entries"] += int(submitted)
        return True


__all__ = ["RegionalSpotPerpConfig", "RegionalSpotPerpStrategy"]
