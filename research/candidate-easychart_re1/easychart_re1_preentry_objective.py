"""Refresh the first meaningful 5/15-minute objective immediately before entry.

A setup may wait many one-minute bars for a footprint or absorption event.  The
original target was frozen at the earlier five-minute interaction, so a newly
confirmed opposing five-minute swing formed during that wait was ignored.  This
produced distant high-R plans a chart trader would not describe as targeting the
first structure.

Before constructing a non-channel plan, this module compares the frozen target
with the latest still-unspent opposing 5m and 15m structures already confirmed
at the entry close, then uses the nearest one.  Channel rotations retain their
explicit channel objective.  The existing 1.0 gross-R rule rejects a trade when
the newly visible first obstacle leaves insufficient room.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, StructureFamily, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow_ob import FlowValidatedOrderBlockDecisionStructureBook
from easychart_re1_flow_ob_responsibility import (
    EasyChartRE1ResponsibleFlowOBBundle,
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
    ResponsiblePhaseFlowMicroEngine,
)


PREENTRY_OBJECTIVE_REFRESH_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_FIRST_STILL_UNSPENT_CONFIRMED_FIVE_OR_FIFTEEN_MINUTE_OPPOSING_STRUCTURE_AVAILABLE_AT_ENTRY_REPLACES_A_MORE_DISTANT_FROZEN_SETUP_TARGET"
)
if PREENTRY_OBJECTIVE_REFRESH_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (PREENTRY_OBJECTIVE_REFRESH_RULE,)


class PreEntryObjectiveRefreshMixin:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._objective_refresh_counts: dict[str, int] = {}

    def _oinc(self, key: str) -> None:
        self._objective_refresh_counts[key] = self._objective_refresh_counts.get(key, 0) + 1

    @staticmethod
    def _closer(side: Side, candidate: float, existing: float) -> bool:
        return candidate < existing if side is Side.LONG else candidate > existing

    def _refresh_objective(self, setup: ScenarioSetup, bar: Candle) -> None:
        if any(member.family is StructureFamily.CHANNEL for member in setup.context_members):
            self._oinc("channel_objective_retained")
            return
        if setup.target_price is None:
            return
        candidates: list[tuple[str, Any, float]] = []
        for source, book in (
            ("5M", getattr(self, "decision_structure", None)),
            ("15M", getattr(self, "structure", None)),
        ):
            if book is None:
                continue
            target = book.target_for(
                setup.side,
                interaction_time_ns=bar.ts_close_ns,
                source_span=setup.context.source_pivot_span,
                current_high=bar.high,
                current_low=bar.low,
            )
            if target is not None:
                candidates.append((source, target[0], target[1]))
        if not candidates:
            self._oinc("no_new_preentry_objective")
            return
        source, zone, price = (
            min(candidates, key=lambda item: item[2])
            if setup.side is Side.LONG
            else max(candidates, key=lambda item: item[2])
        )
        if not self._closer(setup.side, price, setup.target_price):
            self._oinc("frozen_objective_already_nearest")
            return
        old_zone = None if setup.target_zone is None else setup.target_zone.zone_id
        old_price = setup.target_price
        setup.target_zone = zone
        setup.target_price = price
        self._audit(zone)
        self._oinc(f"objective_replaced_by_{source.lower()}")
        self._trace(
            "preentry_objective_refreshed",
            bar.ts_close_ns,
            setup,
            previous_target_zone_id=old_zone,
            previous_target_price=old_price,
            selected_source=source,
            selected_target_zone_id=zone.zone_id,
            selected_target_price=price,
            rule_provenance=PREENTRY_OBJECTIVE_REFRESH_RULE,
        )

    def _make_plan(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ) -> V5TradePlan | None:
        self._refresh_objective(setup, bar)
        return super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )

    @property
    def objective_refresh_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._objective_refresh_counts.items())),
            "rule_provenance": PREENTRY_OBJECTIVE_REFRESH_RULE,
        }


class RefreshPhaseFlowMicroEngine(PreEntryObjectiveRefreshMixin, ResponsiblePhaseFlowMicroEngine):
    pass


class RefreshFlowMajorSwingEngine(PreEntryObjectiveRefreshMixin, ResponsibleFlowMajorSwingEngine):
    pass


class RefreshFlowDecisionAreaEngine(
    PreEntryObjectiveRefreshMixin,
    ResponsibleFlowValidatedDecisionAreaEngine,
):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = FlowValidatedOrderBlockDecisionStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )


class EasyChartRE1PreEntryObjectiveBundle(EasyChartRE1ResponsibleFlowOBBundle):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = RefreshPhaseFlowMicroEngine(
            symbol, tick_size, scale_name="MICRO", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, **kwargs,
        )
        self.major_swing = RefreshFlowMajorSwingEngine(
            symbol, tick_size, scale_name="LIQUIDITY", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, **kwargs,
        )
        self.flow_decision_ob = RefreshFlowDecisionAreaEngine(
            symbol, tick_size, scale_name="FLOW_DECISION_OB", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["preentry_objective_refresh"] = {
            "micro": self.micro.objective_refresh_diagnostics,
            "major_swing": self.major_swing.objective_refresh_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.objective_refresh_diagnostics,
            "rule_provenance": PREENTRY_OBJECTIVE_REFRESH_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1PreEntryObjectiveBundle
