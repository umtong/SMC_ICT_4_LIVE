"""Structure-owned EasyChart auction lifecycle controller.

This module is a structural replacement for the plan-lattice/router line of
research.  Existing engines are used only as causal event sensors.  The policy
unit is one public-structure interaction and exactly one completed mechanism may
own it:

    public wick structure
    -> interaction
    -> rejection / acceptance / defended touch / failed channel
    -> first later price-volume response
    -> one immutable entry, invalidation and destination

Order blocks and FVGs are entry-origin evidence, never standalone strategies.
Channel/trend-line geometry supplies direction, public liquidity and destination.
A deterministic episode registry prevents the same causal interaction from being
relabelled and traded repeatedly.  There is no fitted score, hindsight best plan,
fixed-R target lattice, trade quota, time exit, PnL fallback or symbol rule.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
import math
from typing import Any, Iterable

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from skilled_auction_control_v1 import SkilledAuctionControlV1Bundle


PUBLIC_STRUCTURE_EPISODE_RULE = (
    "RESEARCH_HYPOTHESIS:ONE_PUBLIC_WICK_STRUCTURE_INTERACTION_HAS_ONE_CAUSAL_"
    "AUCTION_LIFECYCLE_AND_ONE_EXECUTABLE_OWNER"
)
STRUCTURAL_DIRECTION_RULE = (
    "RESEARCH_HYPOTHESIS:DIRECTION_IS_THE_COMPLETED_STRUCTURE_TRANSITION_"
    "REJECTION_ACCEPTANCE_DEFENDED_TOUCH_OR_FAILED_CHANNEL_NOT_A_PLAN_SCORE"
)
ENTRY_ORIGIN_RULE = (
    "RESEARCH_HYPOTHESIS:OB_FVG_OR_PRICE_VOLUME_RESPONSE_REFINES_ENTRY_ONLY_"
    "AFTER_CHANNEL_OR_TRENDLINE_DIRECTION_AND_LIQUIDITY_ARE_PUBLIC"
)
DESTINATION_FIRST_RULE = (
    "RESEARCH_HYPOTHESIS:CHOOSE_THE_FIRST_CAUSAL_STRUCTURE_DESTINATION_BEFORE_"
    "REWARD_RISK_AND_REJECT_THE_EPISODE_WHEN_GROSS_RR_IS_BELOW_ONE"
)
for _rule in (
    PUBLIC_STRUCTURE_EPISODE_RULE,
    STRUCTURAL_DIRECTION_RULE,
    ENTRY_ORIGIN_RULE,
    DESTINATION_FIRST_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000
THIRTY_MINUTES_NS = 30 * 60 * 1_000_000_000
SIX_HOURS_NS = 6 * 60 * 60 * 1_000_000_000


def _text(value: Any) -> str:
    return str(getattr(value, "value", value) if value is not None else "").upper()


def _number(value: Any, default: float = math.nan) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _first_number(item: Any, names: Iterable[str]) -> float:
    for name in names:
        value = _number(getattr(item, name, None))
        if math.isfinite(value):
            return value
    return math.nan


def _descriptor(plan: V5TradePlan) -> str:
    fields = (
        getattr(plan, "family", ""),
        getattr(plan, "scenario_path", ""),
        getattr(plan, "scale_name", ""),
        getattr(plan, "entry_style", ""),
        getattr(plan, "entry_kind", ""),
        getattr(plan, "higher_zone_kind", ""),
        getattr(plan, "higher_zone_id", ""),
        getattr(plan, "lower_zone_kind", ""),
        getattr(plan, "lower_zone_id", ""),
        getattr(plan, "target_kind", ""),
        getattr(plan, "target_zone_kind", ""),
        getattr(plan, "target_zone_id", ""),
        getattr(plan, "objective_kind", ""),
        getattr(plan, "objective_id", ""),
    )
    return "|".join(_text(value) for value in fields)


def _has_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def _gross_rr(plan: V5TradePlan) -> float:
    return _first_number(
        plan,
        (
            "gross_reward_risk",
            "gross_rr",
            "planned_gross_rr",
            "reward_risk",
            "planned_reward_risk",
        ),
    )


def _price_geometry(plan: V5TradePlan) -> tuple[float, float, float]:
    entry = _first_number(plan, ("entry_price", "entry", "limit_price"))
    stop = _first_number(plan, ("stop_price", "stop"))
    target = _first_number(plan, ("target_price", "target", "take_profit_price"))
    return entry, stop, target


def _structure_id(plan: V5TradePlan) -> str:
    candidates = (
        getattr(plan, "higher_zone_id", ""),
        getattr(plan, "lower_zone_id", ""),
        getattr(plan, "context_zone_id", ""),
        getattr(plan, "source_zone_id", ""),
    )
    for value in candidates:
        text = _text(value)
        if _has_any(text, ("CHANNEL", "TREND", "DIAGONAL")):
            return text
    lower = _number(getattr(plan, "overlap_lower", math.nan))
    upper = _number(getattr(plan, "overlap_upper", math.nan))
    if math.isfinite(lower) and math.isfinite(upper):
        return f"MOVING-STRUCTURE:{0.5 * (lower + upper):.12g}"
    return f"STRUCTURE:{getattr(plan, 'causal_event_id', plan.plan_id)}"


def _mechanism(plan: V5TradePlan, source: str) -> str | None:
    text = _descriptor(plan)
    family = _text(getattr(plan, "family", ""))
    path = _text(getattr(plan, "scenario_path", ""))

    if family.startswith("SAC_V1_CHANNEL_ACCEPTANCE"):
        return "CHANNEL_ACCEPTANCE"
    if family.startswith("SAC_V1_CHANNEL_REJECTION"):
        return "CHANNEL_REJECTION"

    has_channel = "CHANNEL" in text
    has_trendline = _has_any(text, ("TRENDLINE", "TREND_LINE", "DIAGONAL"))
    if not (has_channel or has_trendline):
        return None

    # A natural-geometry proposal must contain a later entry-origin response.
    # This excludes OB/FVG objects which did not follow a public-structure event.
    if source != "COMPLETE_CHANNEL_CONTROL" and not _has_any(
        text,
        (
            "ORDER_BLOCK",
            "ORDERBLOCK",
            "OB_",
            "FVG",
            "IMBALANCE",
            "RESPONSE",
            "REACCELERATION",
            "CONTROL_TRANSFER",
            "FLOW",
        ),
    ):
        return None

    if has_channel and _has_any(
        text,
        ("FAILURE_CHANNEL", "FAILED_CHANNEL", "CHANNEL_FAILURE", "MIDLINE_FAILURE"),
    ):
        return "CHANNEL_FAILURE"
    if "ACCEPT" in path or "ACCEPT" in text or "BREAK_RETEST" in text:
        return "CHANNEL_ACCEPTANCE" if has_channel else "TRENDLINE_ACCEPTANCE"
    if "REJECT" in path or "REJECT" in text or "FAKEOUT" in text or "TRAP" in text:
        return "CHANNEL_REJECTION" if has_channel else "TRENDLINE_REJECTION"
    if _has_any(path + "|" + text, ("CONTINU", "BOUNCE", "PULLBACK", "FOUR_POINT", "4_POINT")):
        return "CHANNEL_BOUNCE" if has_channel else "TRENDLINE_BOUNCE"
    return None


def _construct_bundle(module_name: str, symbol: str, tick_size: float, minimum_gross_rr: float):
    try:
        module = import_module(module_name)
    except Exception:
        return None
    bundle_type = getattr(module, "MultiScaleScenarioBundle", None)
    if bundle_type is None:
        candidates = [
            value
            for name, value in vars(module).items()
            if name.endswith("Bundle") and isinstance(value, type)
        ]
        bundle_type = candidates[-1] if candidates else None
    if bundle_type is None:
        return None
    attempts = (
        ((symbol, tick_size, minimum_gross_rr), {}),
        ((symbol, tick_size), {"minimum_gross_rr": minimum_gross_rr}),
        ((symbol, tick_size), {}),
        ((), {"symbol": symbol, "tick_size": tick_size, "minimum_gross_rr": minimum_gross_rr}),
    )
    for args, kwargs in attempts:
        try:
            return bundle_type(*args, **kwargs)
        except TypeError:
            continue
    return None


@dataclass(slots=True)
class StructuralProposal:
    plan: V5TradePlan
    source: str
    mechanism: str
    structure_id: str
    interaction_time_ns: int
    observed_time_ns: int
    lower: float
    upper: float


@dataclass(slots=True)
class EpisodeClaim:
    structure_id: str
    interaction_time_ns: int
    terminal_time_ns: int
    lower: float
    upper: float
    owner: str
    plan_id: str


class StructureEpisodeRegistry:
    """Own a public-structure interaction until its causal order lifetime ends."""

    def __init__(self, tick_size: float) -> None:
        self.tick_size = tick_size
        self.claims: list[EpisodeClaim] = []

    @staticmethod
    def _terminal(plan: V5TradePlan, observed_time_ns: int) -> int:
        candidates = (
            "order_expiry_time_ns",
            "pending_expiry_time_ns",
            "valid_until_ns",
            "expiry_time_ns",
            "terminal_time_ns",
        )
        values = [
            int(value)
            for name in candidates
            if (value := _number(getattr(plan, name, math.nan))) == value
        ]
        future = [value for value in values if value >= observed_time_ns]
        return min(future) if future else observed_time_ns + THIRTY_MINUTES_NS

    def _overlaps(self, left: StructuralProposal, right: EpisodeClaim) -> bool:
        if not all(math.isfinite(value) for value in (left.lower, left.upper, right.lower, right.upper)):
            return False
        return max(left.lower, right.lower) <= min(left.upper, right.upper) + self.tick_size

    def existing_owner(self, proposal: StructuralProposal) -> str | None:
        now = proposal.observed_time_ns
        self.claims = [claim for claim in self.claims if claim.terminal_time_ns >= now - FIVE_MINUTES_NS]
        for claim in self.claims:
            same_structure = proposal.structure_id == claim.structure_id
            same_interaction = abs(proposal.interaction_time_ns - claim.interaction_time_ns) <= FIVE_MINUTES_NS
            if same_structure and proposal.interaction_time_ns <= claim.terminal_time_ns:
                return claim.owner
            if same_interaction and self._overlaps(proposal, claim):
                return claim.owner
        return None

    def claim(self, proposal: StructuralProposal, plan: V5TradePlan) -> None:
        terminal = self._terminal(plan, proposal.observed_time_ns)
        terminal = min(terminal, proposal.observed_time_ns + SIX_HOURS_NS)
        self.claims.append(
            EpisodeClaim(
                proposal.structure_id,
                proposal.interaction_time_ns,
                terminal,
                proposal.lower,
                proposal.upper,
                proposal.mechanism,
                plan.plan_id,
            )
        )


class StructuralAuctionControlV2Bundle:
    """One structure-owned stream from complete channel and natural geometry sensors."""

    _PRIORITY = {
        "CHANNEL_ACCEPTANCE": 0,
        "CHANNEL_REJECTION": 1,
        "CHANNEL_FAILURE": 2,
        "TRENDLINE_ACCEPTANCE": 3,
        "TRENDLINE_REJECTION": 4,
        "CHANNEL_BOUNCE": 5,
        "TRENDLINE_BOUNCE": 6,
    }

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = max(1.0, float(minimum_gross_rr))
        self.channel_control = SkilledAuctionControlV1Bundle(
            symbol,
            tick_size,
            self.minimum_gross_rr,
        )
        self.natural_geometry = _construct_bundle(
            "easychart_re1_skilled_integrated",
            symbol,
            tick_size,
            self.minimum_gross_rr,
        )
        self.sources: list[tuple[str, Any]] = [
            ("COMPLETE_CHANNEL_CONTROL", self.channel_control),
        ]
        if self.natural_geometry is not None:
            self.sources.append(("NATURAL_GEOMETRY_RESPONSE", self.natural_geometry))
        self.detectors = self.channel_control.detectors
        self.registry = StructureEpisodeRegistry(tick_size)
        self._plans: list[V5TradePlan] = []
        self._plan_map: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._trace: list[dict[str, Any]] = []

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _proposal(self, plan: V5TradePlan, source: str) -> StructuralProposal | None:
        mechanism = _mechanism(plan, source)
        if mechanism is None:
            self._inc("proposal_not_owned_by_structure_policy")
            return None
        gross_rr = _gross_rr(plan)
        if math.isfinite(gross_rr) and gross_rr + 1e-12 < self.minimum_gross_rr:
            self._inc("destination_does_not_pay_one_gross_r")
            return None
        observed = int(_number(getattr(plan, "observed_time_ns", 0), 0.0))
        interaction = int(_number(getattr(plan, "interaction_time_ns", observed), float(observed)))
        lower = _number(getattr(plan, "overlap_lower", math.nan))
        upper = _number(getattr(plan, "overlap_upper", math.nan))
        return StructuralProposal(
            plan,
            source,
            mechanism,
            _structure_id(plan),
            interaction,
            observed,
            lower,
            upper,
        )

    def _geometry_rank(self, proposal: StructuralProposal) -> tuple[Any, ...]:
        plan = proposal.plan
        entry, _, target = _price_geometry(plan)
        distance = abs(target - entry) if math.isfinite(entry) and math.isfinite(target) else math.inf
        descriptor = _descriptor(plan)
        location_rank = 0 if _has_any(descriptor, ("ORDER_BLOCK", "ORDERBLOCK", "FVG", "IMBALANCE")) else 1
        return (
            self._PRIORITY[proposal.mechanism],
            location_rank,
            distance,
            proposal.observed_time_ns,
            plan.plan_id,
        )

    @staticmethod
    def _same_raw_episode(left: StructuralProposal, right: StructuralProposal, tick_size: float) -> bool:
        same_structure = left.structure_id == right.structure_id
        same_time = abs(left.interaction_time_ns - right.interaction_time_ns) <= FIVE_MINUTES_NS
        overlap = (
            all(math.isfinite(value) for value in (left.lower, left.upper, right.lower, right.upper))
            and max(left.lower, right.lower) <= min(left.upper, right.upper) + tick_size
        )
        return same_structure or (same_time and overlap)

    def _namespace(self, proposal: StructuralProposal) -> V5TradePlan:
        raw = proposal.plan
        existing = self._plan_map.get(raw.plan_id)
        if existing is not None:
            raise RuntimeError(f"duplicate structural raw plan id {raw.plan_id!r} -> {existing!r}")
        plan_id = f"sac-v2-{proposal.mechanism.lower()}-{raw.plan_id}"
        self._plan_map[raw.plan_id] = plan_id
        return replace(
            raw,
            plan_id=plan_id,
            causal_event_id=f"SAC_V2:{proposal.structure_id}:{raw.causal_event_id}",
            family=f"SAC_V2_{proposal.mechanism}:{raw.family}",
        )

    def _route(self, raw: list[tuple[str, V5TradePlan]]) -> list[V5TradePlan]:
        proposals = [
            proposal
            for source, plan in raw
            if (proposal := self._proposal(plan, source)) is not None
        ]
        proposals.sort(key=self._geometry_rank)
        selected: list[StructuralProposal] = []
        for proposal in proposals:
            if any(self._same_raw_episode(proposal, prior, self.tick_size) for prior in selected):
                self._inc("same_bar_episode_already_interpreted")
                continue
            owner = self.registry.existing_owner(proposal)
            if owner is not None:
                self._inc("live_episode_already_owned")
                self._trace.append(
                    {
                        "scenario_kind": "live_public_structure_episode_already_owned",
                        "event_time_ns": proposal.observed_time_ns,
                        "suppressed_plan_id": proposal.plan.plan_id,
                        "structure_id": proposal.structure_id,
                        "proposed_mechanism": proposal.mechanism,
                        "owner": owner,
                        "rule_provenance": PUBLIC_STRUCTURE_EPISODE_RULE,
                    }
                )
                continue
            selected.append(proposal)

        output: list[V5TradePlan] = []
        for proposal in selected:
            plan = self._namespace(proposal)
            self.registry.claim(proposal, plan)
            output.append(plan)
            self._inc(f"owned_{proposal.mechanism.lower()}")
            self._trace.append(
                {
                    "scenario_kind": "public_structure_episode_owned",
                    "event_time_ns": proposal.observed_time_ns,
                    "plan_id": plan.plan_id,
                    "structure_id": proposal.structure_id,
                    "mechanism": proposal.mechanism,
                    "source": proposal.source,
                    "rule_provenance": STRUCTURAL_DIRECTION_RULE,
                }
            )
        output.sort(key=lambda plan: (getattr(plan, "observed_time_ns", 0), plan.plan_id))
        self._plans.extend(output)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        raw: list[tuple[str, V5TradePlan]] = []
        for source_name, bundle in self.sources:
            plans = bundle.on_bar(timeframe_minutes, bar)
            if plans:
                raw.extend((source_name, plan) for plan in plans)
        return self._route(raw)

    def drain_trace(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _, bundle in self.sources:
            drain = getattr(bundle, "drain_trace", None)
            if callable(drain):
                rows.extend(drain())
        for row in rows:
            for key in ("plan_id", "suppressed_plan_id", "owner_plan_id"):
                value = row.get(key)
                if isinstance(value, str) and value in self._plan_map:
                    row[key] = self._plan_map[value]
        output = rows + self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        for _, bundle in self.sources:
            find = getattr(bundle, "find_zone", None)
            if callable(find):
                zone = find(zone_id)
                if zone is not None:
                    return zone
        return None

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        output = []
        for _, bundle in self.sources:
            output.extend(list(getattr(bundle, "setups", ())))
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        sources = {}
        for name, bundle in self.sources:
            sources[name] = getattr(bundle, "diagnostics", {})
        return {
            "structural_auction_control_v2": {
                "counts": dict(sorted(self._counts.items())),
                "active_episode_claims": len(self.registry.claims),
                "owners": tuple(self._PRIORITY),
                "rules": (
                    PUBLIC_STRUCTURE_EPISODE_RULE,
                    STRUCTURAL_DIRECTION_RULE,
                    ENTRY_ORIGIN_RULE,
                    DESTINATION_FIRST_RULE,
                ),
            },
            "sources": sources,
        }


MultiScaleScenarioBundle = StructuralAuctionControlV2Bundle
