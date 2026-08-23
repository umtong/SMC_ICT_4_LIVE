"""Audit compatibility and cross-scale episode routing for EasyChart v5."""
from __future__ import annotations

from typing import Any

from domain import Candle, Side
from contracts_v5 import ScenarioSetup, V5TradePlan
from scenario_engine_v5 import StructureScenarioEngine


class AuditFrame:
    """Compatibility facade for the existing v3 evidence writer."""

    def __init__(self, timeframe_minutes: int) -> None:
        self.timeframe_minutes = timeframe_minutes
        self.bars: list[Candle] = []
        self.zones: list[Any] = []
        self._zone_ids: set[str] = set()
        self.diagnostics: dict[str, int] = {}

    def on_bar(self, bar: Candle) -> None:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("audit bars must arrive in increasing close time")
        self.bars.append(bar)

    def register(self, zone: Any) -> None:
        zone_id = getattr(zone, "zone_id", None)
        if not zone_id or zone_id in self._zone_ids:
            return
        self._zone_ids.add(zone_id)
        self.zones.append(zone)
        kind = getattr(getattr(zone, "kind", None), "value", "UNKNOWN")
        key = f"registered_{str(kind).lower()}"
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    @staticmethod
    def _active(zone: Any) -> bool:
        value = getattr(zone, "active", None)
        if isinstance(value, bool):
            return value
        if value is not None:
            return bool(value)
        return not bool(getattr(zone, "consumed", False))

    def active_zones(self) -> list[Any]:
        return [zone for zone in self.zones if self._active(zone)]


class ResearchScenarioBundleV5:
    """One symbol, two causal structure scales, one plan stream."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.macro = StructureScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = StructureScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = {60: AuditFrame(60), 15: AuditFrame(15), 5: AuditFrame(5), 1: AuditFrame(1)}
        self._claimed_episodes: list[tuple[Side, int, int, float, float]] = []
        self._bundle_trace: list[dict[str, Any]] = []
        self._audit_offsets = {"macro": 0, "micro": 0}

    @property
    def setups(self) -> list[ScenarioSetup]:
        return self.macro.setups + self.micro.setups

    @property
    def plans(self) -> list[V5TradePlan]:
        return self.macro.plans + self.micro.plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "macro": self.macro.diagnostics,
            "macro_structure": self.macro.structure.diagnostics,
            "micro": self.micro.diagnostics,
            "micro_structure": self.micro.structure.diagnostics,
        }

    def _sync_audit(self, key: str, engine: StructureScenarioEngine) -> None:
        start = self._audit_offsets[key]
        for zone in engine.audit_zones[start:]:
            timeframe = getattr(zone, "timeframe_minutes", engine.trigger_minutes)
            destination = timeframe if timeframe in self.detectors else 5
            self.detectors[destination].register(zone)
        self._audit_offsets[key] = len(engine.audit_zones)

    @staticmethod
    def _episode_interval(plan: V5TradePlan) -> tuple[int, int]:
        width = plan.decision_timeframe_minutes * 60_000_000_000
        return plan.interaction_time_ns - width, plan.interaction_time_ns

    def _duplicate_episode(self, plan: V5TradePlan) -> bool:
        start, end = self._episode_interval(plan)
        for side, old_start, old_end, old_lower, old_upper in self._claimed_episodes:
            time_overlap = max(start, old_start) < min(end, old_end)
            price_overlap = (
                max(plan.overlap_lower, old_lower)
                <= min(plan.overlap_upper, old_upper) + self.tick_size
            )
            if side is plan.side and time_overlap and price_overlap:
                return True
        return False

    def _claim_episode(self, plan: V5TradePlan) -> None:
        start, end = self._episode_interval(plan)
        self._claimed_episodes.append(
            (plan.side, start, end, plan.overlap_lower, plan.overlap_upper),
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)
        plans: list[V5TradePlan] = []
        if timeframe_minutes in {60, 15, 5}:
            plans.extend(self.macro.on_bar(timeframe_minutes, bar))
        if timeframe_minutes in {15, 5, 1}:
            plans.extend(self.micro.on_bar(timeframe_minutes, bar))
        self._sync_audit("macro", self.macro)
        self._sync_audit("micro", self.micro)
        ranked = sorted(
            plans,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )
        independent: list[V5TradePlan] = []
        for plan in ranked:
            if self._duplicate_episode(plan):
                self._bundle_trace.append(
                    {
                        "scenario_kind": "causal_episode_duplicate_suppressed",
                        "event_time_ns": plan.observed_time_ns,
                        "scale_name": plan.scale_name,
                        "higher_timeframe_minutes": plan.higher_timeframe_minutes,
                        "decision_timeframe_minutes": plan.decision_timeframe_minutes,
                        "trigger_timeframe_minutes": plan.trigger_timeframe_minutes,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            self._claim_episode(plan)
            independent.append(plan)
        return independent

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.macro.drain_trace() + self.micro.drain_trace() + self._bundle_trace
        self._bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        for detector in self.detectors.values():
            for zone in detector.zones:
                if zone.zone_id == zone_id:
                    return zone
        return self.macro.find_zone(zone_id) or self.micro.find_zone(zone_id)


# Compatibility name consumed by mtf_strategy.py after monkey-patching.
MultiScaleScenarioBundle = ResearchScenarioBundleV5
