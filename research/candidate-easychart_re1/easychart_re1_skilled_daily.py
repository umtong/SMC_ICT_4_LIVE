"""Skilled local policy plus previous-day liquidity sweep opportunities."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_daily_liquidity import DailyLiquiditySweepEngine
from easychart_re1_skilled_integrated import EasyChartRE1SkilledIntegratedBundle


class PreviousDayStructureKind(str, Enum):
    PREVIOUS_DAY_HIGH = "PREVIOUS_DAY_HIGH"
    PREVIOUS_DAY_LOW = "PREVIOUS_DAY_LOW"
    PREVIOUS_DAY_MIDPOINT = "PREVIOUS_DAY_MIDPOINT"


class ExchangeCloseAlignedDailyLiquiditySweepEngine(DailyLiquiditySweepEngine):
    """Assign close-stamped bars correctly and keep plan kinds serializable."""

    @staticmethod
    def _utc_date(time_ns: int):  # type: ignore[no-untyped-def]
        return datetime.fromtimestamp(
            (time_ns - 1) / 1_000_000_000,
            timezone.utc,
        ).date()

    def _level_zone(self, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        zone = super()._level_zone(*args, **kwargs)
        raw = kwargs.get("kind")
        if raw is None:
            raise RuntimeError("previous-day structure kind is missing")
        zone.kind = PreviousDayStructureKind(raw)
        return zone


class EasyChartRE1SkilledDailyBundle:
    """One plan stream: local structure auctions plus daily liquidity traps."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.local = EasyChartRE1SkilledIntegratedBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.daily = ExchangeCloseAlignedDailyLiquiditySweepEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.detectors = self.local.detectors
        self._plans: list[V5TradePlan] = []
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _overlap(self, left: V5TradePlan, right: V5TradePlan) -> bool:
        return (
            left.interaction_time_ns == right.interaction_time_ns
            and max(left.overlap_lower, right.overlap_lower)
            <= min(left.overlap_upper, right.overlap_upper) + self.tick_size
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        local = self.local.on_bar(timeframe_minutes, bar)
        daily = self.daily.on_bar(timeframe_minutes, bar)
        output = list(daily)
        for plan in local:
            owner = next(
                (other for other in daily if self._overlap(plan, other)),
                None,
            )
            if owner is not None:
                self._inc("simultaneous_local_plan_owned_by_daily_liquidity")
                self._trace.append(
                    {
                        "scenario_kind": "simultaneous_episode_owned_by_daily_liquidity",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            output.append(plan)
        unique = {plan.plan_id: plan for plan in output}
        routed = sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                0 if plan.scale_name == "DAILY_LIQUIDITY" else 1,
                plan.observed_time_ns,
                plan.symbol,
                plan.plan_id,
            ),
        )
        self._plans.extend(routed)
        return routed

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.local.drain_trace() + self.daily.drain_trace() + self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.local.find_zone(zone_id) or self.daily.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        return list(self.local.setups) + list(self.daily.setups)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "skilled_daily_router": {
                "counts": dict(sorted(self._counts.items())),
                "daily_episode_priority": "PREVIOUS_DAY_LIQUIDITY",
            },
            "local": self.local.diagnostics,
            "daily_liquidity": self.daily.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1SkilledDailyBundle
