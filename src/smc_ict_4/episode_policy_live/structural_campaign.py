"""One-owner structural auction campaigns.

This module is the strategy centre extracted from the structural-auction-control
and EasyChart research lines.  It deliberately does not import ``policy.py``.
One pre-existing public structure owns the causal campaign; acceptance,
rejection and a failed-acceptance trap are alternative states of that owner,
not separately generated signals.

The API is functional.  :meth:`ParentCampaignOwner.open` and
:meth:`ParentCampaignOwner.advance` return a new immutable snapshot, emitted
events and, at most, one immutable opportunity.  The caller is responsible for
global account arbitration and execution.

There is no clock expiry or time exit.  A campaign ends only because its
structure is invalidated, its committed destination is consumed, its first
physical return fails, or an opportunity is committed.  OB/FVG/retest objects
are entry refinements and cannot create a campaign by themselves.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math
from statistics import median
from typing import Iterable, Literal, Mapping

from .domain import Bar, EntryZone, LiquidityBoundary, PolicyError, stable_id


Side = Literal["LONG", "SHORT"]
OpportunityFamily = Literal[
    "FAILED_AUCTION_REVERSAL",
    "ACCEPTED_AUCTION_CONTINUATION",
]


class CampaignHypothesis(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    ACCEPTANCE = "ACCEPTANCE"
    REJECTION = "REJECTION"
    TRAP = "TRAP"


class CampaignPhase(str, Enum):
    RESOLVING = "RESOLVING"
    WAITING_ACCEPTANCE_HOLD = "WAITING_ACCEPTANCE_HOLD"
    WAITING_ACCEPTANCE_RETEST = "WAITING_ACCEPTANCE_RETEST"
    WAITING_FAILED_ACCEPTANCE_DECISION = "WAITING_FAILED_ACCEPTANCE_DECISION"
    WAITING_RECLAIM = "WAITING_RECLAIM"
    WAITING_REVERSAL_RETEST = "WAITING_REVERSAL_RETEST"
    WAITING_CONTROL_TRANSFER = "WAITING_CONTROL_TRANSFER"
    WAITING_POST_TRANSFER_RETEST = "WAITING_POST_TRANSFER_RETEST"
    OPPORTUNITY_COMMITTED = "OPPORTUNITY_COMMITTED"
    INVALIDATED = "INVALIDATED"
    DESTINATION_SPENT = "DESTINATION_SPENT"
    FIRST_RETURN_REJECTED = "FIRST_RETURN_REJECTED"
    NO_GEOMETRY = "NO_GEOMETRY"

    @property
    def terminal(self) -> bool:
        return self in {
            self.OPPORTUNITY_COMMITTED,
            self.INVALIDATED,
            self.DESTINATION_SPENT,
            self.FIRST_RETURN_REJECTED,
            self.NO_GEOMETRY,
        }


class CampaignEventKind(str, Enum):
    OPENED = "OPENED"
    OUTWARD_BREAK = "OUTWARD_BREAK"
    ACCEPTANCE_HELD = "ACCEPTANCE_HELD"
    ACCEPTANCE_FAILED_TO_TRAP = "ACCEPTANCE_FAILED_TO_TRAP"
    REJECTION_RECLAIMED = "REJECTION_RECLAIMED"
    TRAP_RECLAIMED = "TRAP_RECLAIMED"
    REFINEMENT_LOCKED = "REFINEMENT_LOCKED"
    FIRST_RETURN = "FIRST_RETURN"
    CONTROL_CONFIRMED = "CONTROL_CONFIRMED"
    ENTRY_RETEST = "ENTRY_RETEST"
    DESTINATION_SPENT = "DESTINATION_SPENT"
    INVALIDATED = "INVALIDATED"
    FIRST_RETURN_REJECTED = "FIRST_RETURN_REJECTED"
    OPPORTUNITY_COMMITTED = "OPPORTUNITY_COMMITTED"
    NO_GEOMETRY = "NO_GEOMETRY"


@dataclass(frozen=True, slots=True)
class FlowBaseline:
    """Causal effort/result baseline computed strictly before interaction."""

    median_quote_volume: float
    median_abs_signed_flow: float
    median_abs_body: float
    median_range: float

    def __post_init__(self) -> None:
        for name in (
            "median_quote_volume",
            "median_abs_signed_flow",
            "median_abs_body",
            "median_range",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise PolicyError(f"{name} must be finite and non-negative")
        if self.median_quote_volume <= 0.0:
            raise PolicyError("median_quote_volume must be positive")

    @classmethod
    def from_prior_bars(cls, bars: Iterable[Bar]) -> "FlowBaseline":
        usable = [bar for bar in bars if bar.quote_volume > 0.0]
        if not usable:
            raise PolicyError("a flow baseline requires prior completed quote-volume bars")
        signed = [abs(bar.signed_quote_flow) for bar in usable]
        return cls(
            median_quote_volume=median(bar.quote_volume for bar in usable),
            median_abs_signed_flow=median(signed),
            median_abs_body=median(abs(bar.body) for bar in usable),
            median_range=median(bar.range for bar in usable),
        )


@dataclass(frozen=True, slots=True)
class FlowLedger:
    """Sufficient causal statistics; campaign memory does not grow with age."""

    bars: int = 0
    meaningful_bars: int = 0
    total_quote: float = 0.0
    signed_quote: float = 0.0
    buy_aggressor_quote: float = 0.0
    sell_aggressor_quote: float = 0.0
    aligned_buy_initiative: bool = False
    aligned_sell_initiative: bool = False
    meaningful_buy_flow: bool = False
    meaningful_sell_flow: bool = False

    def observe(self, bar: Bar, baseline: FlowBaseline) -> "FlowLedger":
        if (
            bar.quote_volume <= 0.0
            or bar.taker_buy_quote_volume < 0.0
            or bar.taker_buy_quote_volume > bar.quote_volume * (1.0 + 1e-9)
        ):
            return self
        signed = bar.signed_quote_flow
        active = bar.quote_volume >= baseline.median_quote_volume
        directed = abs(signed) >= baseline.median_abs_signed_flow
        material = abs(bar.body) >= baseline.median_abs_body
        meaningful = active and directed
        return FlowLedger(
            bars=self.bars + 1,
            meaningful_bars=self.meaningful_bars + int(meaningful),
            total_quote=self.total_quote + bar.quote_volume,
            signed_quote=self.signed_quote + signed,
            buy_aggressor_quote=self.buy_aggressor_quote + max(0.0, signed),
            sell_aggressor_quote=self.sell_aggressor_quote + max(0.0, -signed),
            aligned_buy_initiative=(
                self.aligned_buy_initiative
                or (meaningful and material and signed > 0.0 and bar.body > 0.0)
            ),
            aligned_sell_initiative=(
                self.aligned_sell_initiative
                or (meaningful and material and signed < 0.0 and bar.body < 0.0)
            ),
            meaningful_buy_flow=self.meaningful_buy_flow or (meaningful and signed > 0.0),
            meaningful_sell_flow=self.meaningful_sell_flow or (meaningful and signed < 0.0),
        )


@dataclass(frozen=True, slots=True)
class HypothesisGeometry:
    """A structural invalidation and destination selected before entry/RR."""

    destination: LiquidityBoundary
    invalidation_price: float
    target_price: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.invalidation_price):
            raise PolicyError("invalidation_price must be finite")
        if self.target_price is not None and not math.isfinite(self.target_price):
            raise PolicyError("target_price must be finite when supplied")

    @property
    def committed_target(self) -> float:
        # A dynamic destination may be projected by the source-discovery layer,
        # then frozen here.  Falling back to ``price`` is safe for static public
        # liquidity and avoids projecting two timeframes with one serial.
        return self.destination.price if self.target_price is None else self.target_price


@dataclass(frozen=True, slots=True)
class EntryRefinement:
    """One event-local OB, FVG or exact retest; never an episode owner."""

    zone: EntryZone
    side: Side
    structural_stop: float
    invalidation_id: str

    def __post_init__(self) -> None:
        if self.side not in {"LONG", "SHORT"}:
            raise PolicyError("refinement side must be LONG or SHORT")
        if not math.isfinite(self.structural_stop):
            raise PolicyError("structural_stop must be finite")
        if not self.invalidation_id:
            raise PolicyError("invalidation_id cannot be empty")


@dataclass(frozen=True, slots=True)
class CampaignObservation:
    """One causally ordered completed minute bar presented to the FSM.

    Every bar contributes to physical-return and aggressor-flow evidence.
    ``decision_bar`` is the completed aggregate candle made observable by the
    current minute close.  Auction acceptance/rejection is decided from that
    aggregate candle; physical first-return, refinement and flow remain owned
    by ``bar``.
    """

    sequence: int
    structure_serial: int
    bar: Bar
    is_decision_close: bool = False
    decision_bar: Bar | None = None
    refinement: EntryRefinement | None = None
    acceptance_geometry: HypothesisGeometry | None = None
    reversal_geometry: HypothesisGeometry | None = None
    acceptance_destination_fresh: bool = True
    reversal_destination_fresh: bool = True
    acceptance_route_clear: bool = True
    reversal_route_clear: bool = True

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise PolicyError("observation sequence cannot be negative")
        if self.bar.interval_minutes != 1:
            raise PolicyError("campaign observation bar must be one completed minute")
        if not self.is_decision_close:
            if self.decision_bar is not None:
                raise PolicyError("decision_bar is valid only on a decision close")
            return
        decision = self.decision_bar
        if decision is None:
            raise PolicyError("a decision close requires its completed aggregate decision_bar")
        if decision.symbol != self.bar.symbol:
            raise PolicyError("decision_bar symbol differs from minute observation")
        if decision.interval_minutes <= 1:
            raise PolicyError("decision_bar must aggregate more than one minute")
        if decision.close_time_ns != self.bar.close_time_ns:
            raise PolicyError("decision_bar and minute observation must have the same close time")
        if decision.open_time_ns > self.bar.open_time_ns:
            raise PolicyError("decision_bar does not temporally cover the closing minute")
        if decision.close_time_ns > self.bar.close_time_ns:
            raise PolicyError("decision_bar contains future information")
        if decision.low > self.bar.low or decision.high < self.bar.high:
            raise PolicyError("decision_bar price range does not cover the closing minute")
        if not math.isclose(decision.close, self.bar.close, rel_tol=0.0, abs_tol=1e-12):
            raise PolicyError("decision_bar close differs from the closing minute")


@dataclass(frozen=True, slots=True)
class CampaignEvent:
    kind: CampaignEventKind
    campaign_id: str
    observed_time_ns: int
    hypothesis: CampaignHypothesis
    phase: CampaignPhase
    reason: str
    details: tuple[tuple[str, str | int | float], ...] = ()


@dataclass(frozen=True, slots=True)
class EpisodeFlowControl:
    mechanism: Literal[
        "AGGRESSOR_INITIATIVE_CONTROL",
        "OPPOSING_AGGRESSION_ABSORBED",
        "INITIATIVE_AFTER_ABSORPTION",
    ]
    episode_bars: int
    response_bars: int
    total_quote: float
    aligned_taker_quote: float
    adverse_taker_quote: float
    cumulative_signed_for_side: float
    adverse_penetration: float
    recovery_from_extreme: float
    final_control_progress: float


@dataclass(frozen=True, slots=True)
class StructuralOpportunity:
    """Immutable pre-entry geometry compatible with ``domain.TradePlan``.

    ``as_trade_plan_fields`` intentionally omits ``expires_time_ns``.  Pending
    validity must be driven by later physical invalidation/target consumption,
    not by an arbitrary clock.
    """

    episode_id: str
    plan_id: str
    symbol: str
    family: OpportunityFamily
    side: Side
    decision_time_ns: int
    first_return_detached_time_ns: int
    first_return_time_ns: int
    control_transfer_time_ns: int
    entry_retest_detached_time_ns: int | None
    entry_retest_time_ns: int
    entry: float
    stop: float
    target: float
    source_boundary_id: str
    destination_boundary_id: str
    entry_zone: EntryZone
    flow_control: EpisodeFlowControl
    hypothesis: CampaignHypothesis
    owner_evidence: Mapping[str, object] = field(default_factory=dict)

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_distance(self) -> float:
        return abs(self.target - self.entry)

    @property
    def gross_rr(self) -> float:
        return self.reward_distance / self.risk_distance

    def as_trade_plan_fields(self) -> Mapping[str, object]:
        return {
            "episode_id": self.episode_id,
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "family": self.family,
            "side": self.side,
            "decision_time_ns": self.decision_time_ns,
            "first_return_time_ns": self.first_return_time_ns,
            "control_transfer_time_ns": self.control_transfer_time_ns,
            "entry_retest_time_ns": self.entry_retest_time_ns,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "source_boundary_id": self.source_boundary_id,
            "destination_boundary_id": self.destination_boundary_id,
            "entry_zone": self.entry_zone,
            "evidence": {
                **dict(self.owner_evidence),
                "campaign_hypothesis": self.hypothesis.value,
                "flow_mechanism": self.flow_control.mechanism,
                "flow_episode_bars": self.flow_control.episode_bars,
                "flow_response_bars": self.flow_control.response_bars,
                "first_return_detached_time_ns": (
                    self.first_return_detached_time_ns
                ),
                "first_return_time_ns": self.first_return_time_ns,
                "control_transfer_time_ns": self.control_transfer_time_ns,
                "entry_retest_detached_time_ns": (
                    self.entry_retest_detached_time_ns
                ),
                "entry_retest_time_ns": self.entry_retest_time_ns,
            },
        }


@dataclass(frozen=True, slots=True)
class CampaignSeed:
    source: LiquidityBoundary
    interaction: CampaignObservation
    flow_baseline: FlowBaseline
    tick_size: float
    acceptance: HypothesisGeometry | None = None
    reversal: HypothesisGeometry | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.tick_size) or self.tick_size <= 0.0:
            raise PolicyError("tick_size must be positive and finite")


@dataclass(frozen=True, slots=True)
class CampaignSnapshot:
    campaign_id: str
    source: LiquidityBoundary
    # These are causal candidates observed before a hypothesis settles.  Only
    # ``committed_geometry`` can govern entry, invalidation or destination use.
    acceptance: HypothesisGeometry | None
    reversal: HypothesisGeometry | None
    committed_geometry: HypothesisGeometry | None
    tick_size: float
    flow_baseline: FlowBaseline
    hypothesis: CampaignHypothesis
    phase: CampaignPhase
    interaction_time_ns: int
    interaction_open_time_ns: int
    interaction_sequence: int
    last_time_ns: int
    last_sequence: int
    last_structure_serial: int
    acceptance_break_time_ns: int | None
    confirmation_time_ns: int | None
    episode_low: float
    episode_high: float
    episode_flow: FlowLedger
    response_flow: FlowLedger
    refinement: EntryRefinement | None = None
    first_return_detached_time_ns: int | None = None
    first_return_time_ns: int | None = None
    first_return_low: float | None = None
    first_return_high: float | None = None
    post_return_low: float | None = None
    post_return_high: float | None = None
    control_transfer_time_ns: int | None = None
    control_transfer_flow: EpisodeFlowControl | None = None
    entry_retest_detached_time_ns: int | None = None
    entry_retest_time_ns: int | None = None
    acceptance_route_clear: bool = True
    reversal_route_clear: bool = True
    terminal_reason: str | None = None

    @property
    def terminal(self) -> bool:
        return self.phase.terminal

    @property
    def outward_side(self) -> Side:
        return "LONG" if self.source.side == "HIGH" else "SHORT"

    @property
    def reversal_side(self) -> Side:
        return "SHORT" if self.source.side == "HIGH" else "LONG"

    @property
    def active_side(self) -> Side | None:
        if self.hypothesis is CampaignHypothesis.ACCEPTANCE:
            return self.outward_side
        if self.hypothesis in {CampaignHypothesis.REJECTION, CampaignHypothesis.TRAP}:
            return self.reversal_side
        return None


@dataclass(frozen=True, slots=True)
class CampaignTransition:
    previous: CampaignSnapshot | None
    current: CampaignSnapshot
    events: tuple[CampaignEvent, ...]
    opportunity: StructuralOpportunity | None = None


def _event(
    state: CampaignSnapshot,
    kind: CampaignEventKind,
    time_ns: int,
    reason: str,
    **details: str | int | float,
) -> CampaignEvent:
    return CampaignEvent(
        kind=kind,
        campaign_id=state.campaign_id,
        observed_time_ns=time_ns,
        hypothesis=state.hypothesis,
        phase=state.phase,
        reason=reason,
        details=tuple(sorted(details.items())),
    )


class ParentCampaignOwner:
    """Pure one-owner structural-auction finite-state machine."""

    @classmethod
    def open(cls, seed: CampaignSeed) -> CampaignTransition:
        obs = seed.interaction
        source = seed.source
        bar = obs.bar
        if source.symbol != bar.symbol:
            raise PolicyError("source and interaction symbol differ")
        lower, upper = source.band_at(obs.structure_serial)
        if not (bar.low <= upper and bar.high >= lower):
            raise PolicyError("a campaign can open only on first physical source contact")
        if source.observed_time_ns > bar.open_time_ns:
            raise PolicyError("campaign source was not observable before interaction bar opened")
        if not source.is_fresh(bar.open_time_ns):
            raise PolicyError("campaign source was already consumed at interaction")
        for hypothesis, geometry in (
            (CampaignHypothesis.ACCEPTANCE, seed.acceptance),
            (CampaignHypothesis.REJECTION, seed.reversal),
        ):
            if geometry is not None:
                cls._validate_geometry(
                    source,
                    hypothesis,
                    geometry,
                    lower,
                    upper,
                    bar.open_time_ns,
                )
        campaign_id = stable_id(
            source.symbol,
            source.boundary_id,
            bar.close_time_ns,
            prefix="structural-campaign-",
        )
        base = CampaignSnapshot(
            campaign_id=campaign_id,
            source=source,
            acceptance=seed.acceptance,
            reversal=seed.reversal,
            committed_geometry=None,
            tick_size=seed.tick_size,
            flow_baseline=seed.flow_baseline,
            hypothesis=CampaignHypothesis.UNRESOLVED,
            phase=CampaignPhase.RESOLVING,
            interaction_time_ns=bar.close_time_ns,
            interaction_open_time_ns=bar.open_time_ns,
            interaction_sequence=obs.sequence,
            last_time_ns=bar.open_time_ns,
            last_sequence=obs.sequence - 1,
            last_structure_serial=obs.structure_serial,
            acceptance_break_time_ns=None,
            confirmation_time_ns=None,
            episode_low=bar.low,
            episode_high=bar.high,
            episode_flow=FlowLedger(),
            response_flow=FlowLedger(),
        )
        opened = _event(
            base,
            CampaignEventKind.OPENED,
            bar.close_time_ns,
            "first_physical_contact_with_preexisting_structure",
            source_boundary_id=source.boundary_id,
        )
        transition = cls._advance(base, obs)
        return CampaignTransition(
            previous=None,
            current=transition.current,
            events=(opened,) + transition.events,
            opportunity=transition.opportunity,
        )

    @classmethod
    def advance(
        cls,
        state: CampaignSnapshot,
        observation: CampaignObservation,
    ) -> CampaignTransition:
        if state.terminal:
            raise PolicyError("a terminal structural campaign cannot advance")
        return cls._advance(state, observation)

    @staticmethod
    def _validate_geometry(
        source: LiquidityBoundary,
        hypothesis: CampaignHypothesis,
        geometry: HypothesisGeometry,
        lower: float,
        upper: float,
        visible_time_ns: int,
    ) -> None:
        destination = geometry.destination
        if destination.symbol != source.symbol:
            raise PolicyError("geometry destination symbol differs from source")
        if destination.observed_time_ns > visible_time_ns:
            raise PolicyError("geometry destination was not causally visible")
        if not destination.is_fresh(visible_time_ns):
            raise PolicyError("geometry destination was already consumed")
        outward: Side = "LONG" if source.side == "HIGH" else "SHORT"
        side = outward if hypothesis is CampaignHypothesis.ACCEPTANCE else (
            "SHORT" if outward == "LONG" else "LONG"
        )
        target = geometry.committed_target
        if side == "LONG":
            valid = target > upper and geometry.invalidation_price < upper
        else:
            valid = target < lower and geometry.invalidation_price > lower
        if not valid:
            raise PolicyError("candidate stop/destination geometry contradicts hypothesis")

    @classmethod
    def _advance(
        cls,
        prior: CampaignSnapshot,
        obs: CampaignObservation,
    ) -> CampaignTransition:
        bar = obs.bar
        decision_bar = obs.decision_bar
        if bar.symbol != prior.source.symbol:
            raise PolicyError("observation symbol differs from campaign")
        if bar.interval_minutes != 1:
            raise PolicyError("structural campaigns consume one completed minute bar per observation")
        if obs.sequence <= prior.last_sequence:
            raise PolicyError("campaign observations must have strictly increasing sequence")
        if bar.close_time_ns < prior.last_time_ns:
            raise PolicyError("campaign event time cannot move backwards")

        episode_flow = prior.episode_flow
        response_flow = prior.response_flow
        episode_flow = episode_flow.observe(bar, prior.flow_baseline)
        if prior.confirmation_time_ns is not None and bar.close_time_ns > prior.confirmation_time_ns:
            response_flow = response_flow.observe(bar, prior.flow_baseline)
        state = replace(
            prior,
            last_time_ns=bar.close_time_ns,
            last_sequence=obs.sequence,
            last_structure_serial=obs.structure_serial,
            episode_low=min(prior.episode_low, bar.low),
            episode_high=max(prior.episode_high, bar.high),
            episode_flow=episode_flow,
            response_flow=response_flow,
            acceptance_route_clear=obs.acceptance_route_clear,
            reversal_route_clear=obs.reversal_route_clear,
        )
        events: list[CampaignEvent] = []
        lower, upper = state.source.band_at(obs.structure_serial)

        acceptance_locked = (
            state.phase in {
                CampaignPhase.WAITING_ACCEPTANCE_HOLD,
                CampaignPhase.WAITING_FAILED_ACCEPTANCE_DECISION,
            }
            or (
                state.hypothesis is CampaignHypothesis.ACCEPTANCE
                and state.committed_geometry is not None
            )
        )
        reversal_locked = (
            state.hypothesis in {CampaignHypothesis.REJECTION, CampaignHypothesis.TRAP}
            and state.committed_geometry is not None
        )
        if not acceptance_locked:
            if obs.acceptance_geometry is not None:
                cls._validate_geometry(
                    state.source,
                    CampaignHypothesis.ACCEPTANCE,
                    obs.acceptance_geometry,
                    lower,
                    upper,
                    bar.close_time_ns,
                )
            state = replace(state, acceptance=obs.acceptance_geometry)
        if not reversal_locked:
            if obs.reversal_geometry is not None:
                cls._validate_geometry(
                    state.source,
                    CampaignHypothesis.REJECTION,
                    obs.reversal_geometry,
                    lower,
                    upper,
                    bar.close_time_ns,
                )
            state = replace(state, reversal=obs.reversal_geometry)

        if cls._active_destination_spent(state, obs):
            ended = replace(
                state,
                phase=CampaignPhase.DESTINATION_SPENT,
                terminal_reason="committed_destination_consumed_before_entry",
            )
            return CampaignTransition(
                prior,
                ended,
                (_event(ended, CampaignEventKind.DESTINATION_SPENT, bar.close_time_ns, ended.terminal_reason),),
            )

        # A newly observed footprint may be locked only after a directional
        # hypothesis owns the campaign.  It cannot create that hypothesis.
        if state.phase in {
            CampaignPhase.WAITING_ACCEPTANCE_RETEST,
            CampaignPhase.WAITING_REVERSAL_RETEST,
        } and state.refinement is None and obs.refinement is not None:
            if obs.refinement.side != state.active_side:
                # No hypothesis transition has occurred yet in this
                # observation, so an opposite-side refinement is a genuine
                # caller contract violation rather than stale evidence.
                cls._validate_refinement(state, obs.refinement, bar.close_time_ns)
            cls._validate_refinement(state, obs.refinement, bar.close_time_ns)
            state = replace(state, refinement=obs.refinement)
            events.append(
                _event(
                    state,
                    CampaignEventKind.REFINEMENT_LOCKED,
                    bar.close_time_ns,
                    "first_event_local_entry_refinement_locked",
                    zone_kind=obs.refinement.zone.kind,
                )
            )

        if state.phase is CampaignPhase.RESOLVING and obs.is_decision_close:
            assert decision_bar is not None
            outward = state.outward_side
            if cls._closes_outside(
                decision_bar,
                outward,
                lower,
                upper,
                source=state.source,
            ):
                state = replace(
                    state,
                    phase=CampaignPhase.WAITING_ACCEPTANCE_HOLD,
                    acceptance_break_time_ns=bar.close_time_ns,
                )
                events.append(
                    _event(state, CampaignEventKind.OUTWARD_BREAK, bar.close_time_ns, "completed_decision_close_outside_source")
                )
            elif cls._reclaimed(state, decision_bar, state.reversal_side, lower, upper):
                if state.reversal is None:
                    ended = cls._no_geometry(
                        state,
                        "rejection_settled_without_causal_reversal_destination",
                    )
                    events.append(
                        _event(ended, CampaignEventKind.NO_GEOMETRY, bar.close_time_ns, ended.terminal_reason or "")
                    )
                    return CampaignTransition(prior, ended, tuple(events))
                state = replace(
                    state,
                    hypothesis=CampaignHypothesis.REJECTION,
                    phase=CampaignPhase.WAITING_REVERSAL_RETEST,
                    confirmation_time_ns=bar.close_time_ns,
                    response_flow=FlowLedger(),
                    committed_geometry=state.reversal,
                )
                events.append(
                    _event(state, CampaignEventKind.REJECTION_RECLAIMED, bar.close_time_ns, "outward_probe_reclaimed_by_completed_decision_bar")
                )

        elif (
            state.phase is CampaignPhase.WAITING_ACCEPTANCE_HOLD
            and obs.is_decision_close
            and bar.close_time_ns > (state.acceptance_break_time_ns or -1)
        ):
            assert decision_bar is not None
            expected_hold_time_ns = (
                int(state.acceptance_break_time_ns or 0)
                + decision_bar.interval_minutes * 60_000_000_000
            )
            if bar.close_time_ns != expected_hold_time_ns:
                ended = cls._no_geometry(
                    state,
                    "acceptance_hold_was_not_immediate_next_decision_bar",
                )
                events.append(
                    _event(
                        ended,
                        CampaignEventKind.NO_GEOMETRY,
                        bar.close_time_ns,
                        ended.terminal_reason or "",
                    )
                )
                return CampaignTransition(prior, ended, tuple(events))
            held = cls._opened_and_closed_outside(
                decision_bar,
                state.outward_side,
                lower,
                upper,
            )
            if held:
                if state.acceptance is None:
                    ended = cls._no_geometry(
                        state,
                        "acceptance_settled_without_causal_acceptance_destination",
                    )
                    events.append(
                        _event(ended, CampaignEventKind.NO_GEOMETRY, bar.close_time_ns, ended.terminal_reason or "")
                    )
                    return CampaignTransition(prior, ended, tuple(events))
                state = replace(
                    state,
                    hypothesis=CampaignHypothesis.ACCEPTANCE,
                    phase=CampaignPhase.WAITING_ACCEPTANCE_RETEST,
                    confirmation_time_ns=bar.close_time_ns,
                    response_flow=FlowLedger(),
                    committed_geometry=state.acceptance,
                )
                events.append(
                    _event(state, CampaignEventKind.ACCEPTANCE_HELD, bar.close_time_ns, "next_completed_decision_bar_held_outside")
                )
            else:
                state = cls._to_trap(state, bar.close_time_ns)
                events.append(
                    _event(state, CampaignEventKind.ACCEPTANCE_FAILED_TO_TRAP, bar.close_time_ns, "accepted_break_failed_next_decision_hold")
                )
                if cls._reclaim_close(decision_bar, state.reversal_side, lower, upper):
                    state = cls._confirm_trap_reclaim(state, bar.close_time_ns)
                    if state.phase is CampaignPhase.NO_GEOMETRY:
                        events.append(
                            _event(state, CampaignEventKind.NO_GEOMETRY, bar.close_time_ns, state.terminal_reason or "")
                        )
                        return CampaignTransition(prior, state, tuple(events))
                    events.append(
                        _event(state, CampaignEventKind.TRAP_RECLAIMED, bar.close_time_ns, "failed_acceptance_reclaimed_on_same_decision_bar")
                    )

        elif state.phase is CampaignPhase.WAITING_RECLAIM and obs.is_decision_close:
            assert decision_bar is not None
            if cls._reclaim_close(decision_bar, state.reversal_side, lower, upper):
                state = cls._confirm_trap_reclaim(state, bar.close_time_ns)
                if state.phase is CampaignPhase.NO_GEOMETRY:
                    events.append(
                        _event(state, CampaignEventKind.NO_GEOMETRY, bar.close_time_ns, state.terminal_reason or "")
                    )
                    return CampaignTransition(prior, state, tuple(events))
                events.append(
                    _event(state, CampaignEventKind.TRAP_RECLAIMED, bar.close_time_ns, "same_campaign_delayed_trap_reclaim")
                )

        elif (
            state.phase is CampaignPhase.WAITING_FAILED_ACCEPTANCE_DECISION
            and obs.is_decision_close
        ):
            assert decision_bar is not None
            state = cls._to_trap(state, bar.close_time_ns)
            events.append(
                _event(
                    state,
                    CampaignEventKind.ACCEPTANCE_FAILED_TO_TRAP,
                    bar.close_time_ns,
                    "failed_first_retest_confirmed_by_completed_decision_bar",
                )
            )
            if cls._reclaim_close(
                decision_bar,
                state.reversal_side,
                lower,
                upper,
            ):
                state = cls._confirm_trap_reclaim(state, bar.close_time_ns)
                if state.phase is CampaignPhase.NO_GEOMETRY:
                    events.append(
                        _event(
                            state,
                            CampaignEventKind.NO_GEOMETRY,
                            bar.close_time_ns,
                            state.terminal_reason or "",
                        )
                    )
                    return CampaignTransition(prior, state, tuple(events))
                events.append(
                    _event(
                        state,
                        CampaignEventKind.TRAP_RECLAIMED,
                        bar.close_time_ns,
                        "failed_first_retest_reclaimed_by_completed_decision_bar",
                    )
                )

        elif state.phase is CampaignPhase.WAITING_ACCEPTANCE_RETEST:
            if cls._structurally_invalidated(state, bar):
                ended = replace(
                    state,
                    phase=CampaignPhase.INVALIDATED,
                    terminal_reason="committed_acceptance_invalidation_breached",
                )
                events.append(
                    _event(
                        ended,
                        CampaignEventKind.INVALIDATED,
                        bar.close_time_ns,
                        ended.terminal_reason,
                    )
                )
                return CampaignTransition(prior, ended, tuple(events))
            if bar.close_time_ns <= (state.confirmation_time_ns or -1):
                return CampaignTransition(prior, state, tuple(events))
            if state.first_return_detached_time_ns is None:
                if cls._fully_detached(
                    bar,
                    state.outward_side,
                    lower,
                    upper,
                ):
                    state = replace(
                        state,
                        first_return_detached_time_ns=bar.close_time_ns,
                    )
                return CampaignTransition(prior, state, tuple(events))
            if cls._touches_band(bar, lower, upper):
                events.append(
                    _event(state, CampaignEventKind.FIRST_RETURN, bar.close_time_ns, "first_physical_return_to_accepted_source")
                )
                if not cls._closes_outside(
                    bar,
                    state.outward_side,
                    lower,
                    upper,
                ):
                    state = cls._await_failed_acceptance_decision(
                        state,
                        bar.close_time_ns,
                    )
                    if decision_bar is not None:
                        state = cls._to_trap(state, bar.close_time_ns)
                        events.append(
                            _event(
                                state,
                                CampaignEventKind.ACCEPTANCE_FAILED_TO_TRAP,
                                bar.close_time_ns,
                                "failed_first_retest_confirmed_by_completed_decision_bar",
                            )
                        )
                        if cls._reclaim_close(
                            decision_bar,
                            state.reversal_side,
                            lower,
                            upper,
                        ):
                            state = cls._confirm_trap_reclaim(state, bar.close_time_ns)
                            if state.phase is CampaignPhase.NO_GEOMETRY:
                                events.append(
                                    _event(state, CampaignEventKind.NO_GEOMETRY, bar.close_time_ns, state.terminal_reason or "")
                                )
                                return CampaignTransition(prior, state, tuple(events))
                            events.append(
                                _event(
                                    state,
                                    CampaignEventKind.TRAP_RECLAIMED,
                                    bar.close_time_ns,
                                    "failed_first_retest_reclaimed_on_same_decision_close",
                                )
                            )
                else:
                    return cls._resolve_first_return(prior, state, obs, events)

        elif state.phase is CampaignPhase.WAITING_REVERSAL_RETEST:
            if cls._structurally_invalidated(state, bar):
                ended = replace(
                    state,
                    phase=CampaignPhase.INVALIDATED,
                    terminal_reason="committed_reversal_invalidation_breached",
                )
                events.append(_event(ended, CampaignEventKind.INVALIDATED, bar.close_time_ns, ended.terminal_reason))
                return CampaignTransition(prior, ended, tuple(events))
            refinement = state.refinement
            if (
                refinement is not None
                and state.first_return_detached_time_ns is None
                and bar.close_time_ns > max(
                    state.confirmation_time_ns or -1,
                    refinement.zone.observed_time_ns,
                )
            ):
                if cls._fully_detached(
                    bar,
                    state.reversal_side,
                    refinement.zone.lower,
                    refinement.zone.upper,
                ):
                    state = replace(
                        state,
                        first_return_detached_time_ns=bar.close_time_ns,
                    )
                return CampaignTransition(prior, state, tuple(events))
            if (
                refinement is not None
                and state.first_return_detached_time_ns is not None
                and bar.close_time_ns > max(
                    state.first_return_detached_time_ns,
                    state.confirmation_time_ns or -1,
                    refinement.zone.observed_time_ns,
                )
                and cls._touches_zone(bar, refinement.zone)
            ):
                events.append(
                    _event(state, CampaignEventKind.FIRST_RETURN, bar.close_time_ns, "first_physical_return_to_event_local_refinement")
                )
                return cls._resolve_first_return(prior, state, obs, events)

        elif state.phase is CampaignPhase.WAITING_CONTROL_TRANSFER:
            if cls._structurally_invalidated(state, bar):
                ended = replace(
                    state,
                    phase=CampaignPhase.INVALIDATED,
                    terminal_reason="committed_structure_invalidated_before_control_transfer",
                )
                events.append(
                    _event(
                        ended,
                        CampaignEventKind.INVALIDATED,
                        bar.close_time_ns,
                        ended.terminal_reason,
                    )
                )
                return CampaignTransition(prior, ended, tuple(events))
            state = replace(
                state,
                post_return_low=min(
                    state.post_return_low
                    if state.post_return_low is not None
                    else bar.low,
                    bar.low,
                ),
                post_return_high=max(
                    state.post_return_high
                    if state.post_return_high is not None
                    else bar.high,
                    bar.high,
                ),
            )
            if cls._is_control_transfer_bar(state, bar):
                control = cls._control_transfer_flow(state, bar)
                state = replace(
                    state,
                    control_transfer_time_ns=bar.close_time_ns,
                    control_transfer_flow=control,
                )
                events.append(
                    _event(
                        state,
                        CampaignEventKind.CONTROL_CONFIRMED,
                        bar.close_time_ns,
                        "post_return_reacceleration_broke_local_structure_with_meaningful_initiative",
                        mechanism=control.mechanism,
                    )
                )
                if state.hypothesis in {
                    CampaignHypothesis.REJECTION,
                    CampaignHypothesis.TRAP,
                }:
                    armed = replace(
                        state,
                        phase=CampaignPhase.WAITING_POST_TRANSFER_RETEST,
                        entry_retest_detached_time_ns=(
                            bar.close_time_ns
                            if state.refinement is not None
                            and cls._fully_detached(
                                bar,
                                state.active_side,
                                state.refinement.zone.lower,
                                state.refinement.zone.upper,
                            )
                            else None
                        ),
                    )
                    return CampaignTransition(prior, armed, tuple(events))
                return cls._commit_control_transfer(
                    prior,
                    replace(state, entry_retest_time_ns=bar.close_time_ns),
                    bar,
                    control,
                    events,
                )

        elif state.phase is CampaignPhase.WAITING_POST_TRANSFER_RETEST:
            if cls._structurally_invalidated(state, bar):
                ended = replace(
                    state,
                    phase=CampaignPhase.INVALIDATED,
                    terminal_reason=(
                        "committed_structure_invalidated_before_post_transfer_retest"
                    ),
                )
                events.append(
                    _event(
                        ended,
                        CampaignEventKind.INVALIDATED,
                        bar.close_time_ns,
                        ended.terminal_reason,
                    )
                )
                return CampaignTransition(prior, ended, tuple(events))
            refinement = state.refinement
            if refinement is None or state.control_transfer_flow is None:
                raise PolicyError(
                    "post-transfer retest requires frozen refinement and control"
                )
            if bar.close_time_ns <= (state.control_transfer_time_ns or -1):
                return CampaignTransition(prior, state, tuple(events))
            if state.entry_retest_detached_time_ns is None:
                if cls._fully_detached(
                    bar,
                    state.active_side,
                    refinement.zone.lower,
                    refinement.zone.upper,
                ):
                    state = replace(
                        state,
                        entry_retest_detached_time_ns=bar.close_time_ns,
                    )
                return CampaignTransition(prior, state, tuple(events))
            if cls._touches_zone(bar, refinement.zone):
                events.append(
                    _event(
                        state,
                        CampaignEventKind.ENTRY_RETEST,
                        bar.close_time_ns,
                        "first_post_transfer_return_to_locked_entry_refinement",
                    )
                )
                held = (
                    bar.close > refinement.zone.upper
                    if state.active_side == "LONG"
                    else bar.close < refinement.zone.lower
                )
                if not held:
                    ended = replace(
                        state,
                        phase=CampaignPhase.FIRST_RETURN_REJECTED,
                        entry_retest_time_ns=bar.close_time_ns,
                        terminal_reason=(
                            "first_post_transfer_retest_failed_to_hold_controlled_side"
                        ),
                    )
                    events.append(
                        _event(
                            ended,
                            CampaignEventKind.FIRST_RETURN_REJECTED,
                            bar.close_time_ns,
                            ended.terminal_reason,
                        )
                    )
                    return CampaignTransition(prior, ended, tuple(events))
                return cls._commit_control_transfer(
                    prior,
                    replace(state, entry_retest_time_ns=bar.close_time_ns),
                    bar,
                    state.control_transfer_flow,
                    events,
                )

        # A footprint formed on the confirmation bar is eligible for a later
        # return.  It was not eligible at the beginning of this transition,
        # because the directional hypothesis did not own the campaign yet.
        if state.phase in {
            CampaignPhase.WAITING_ACCEPTANCE_RETEST,
            CampaignPhase.WAITING_REVERSAL_RETEST,
        } and state.refinement is None and obs.refinement is not None:
            if obs.refinement.side == state.active_side:
                cls._validate_refinement(state, obs.refinement, bar.close_time_ns)
                state = replace(state, refinement=obs.refinement)
                events.append(
                    _event(
                        state,
                        CampaignEventKind.REFINEMENT_LOCKED,
                        bar.close_time_ns,
                        "first_event_local_entry_refinement_locked",
                        zone_kind=obs.refinement.zone.kind,
                    )
                )
            elif state.active_side == prior.active_side:
                # Without a same-observation hypothesis transition this is not
                # stale evidence; preserve the strict external contract.
                cls._validate_refinement(state, obs.refinement, bar.close_time_ns)
            # Otherwise the hypothesis flipped on this bar.  The refinement
            # belonged to the prior side and is intentionally ignored; it is
            # never relabelled for the new owner.

        # A destination crossed by the same bar that resolves the hypothesis
        # is already spent; it cannot become a post-hoc reward objective.
        if cls._active_destination_spent(state, obs):
            ended = replace(
                state,
                phase=CampaignPhase.DESTINATION_SPENT,
                terminal_reason="committed_destination_consumed_before_entry",
            )
            events.append(
                _event(ended, CampaignEventKind.DESTINATION_SPENT, bar.close_time_ns, ended.terminal_reason)
            )
            return CampaignTransition(prior, ended, tuple(events))

        return CampaignTransition(prior, state, tuple(events))

    @staticmethod
    def _to_trap(state: CampaignSnapshot, time_ns: int) -> CampaignSnapshot:
        return replace(
            state,
            hypothesis=CampaignHypothesis.TRAP,
            phase=CampaignPhase.WAITING_RECLAIM,
            confirmation_time_ns=None,
            response_flow=FlowLedger(),
            refinement=None,
            first_return_detached_time_ns=None,
            first_return_time_ns=None,
            first_return_low=None,
            first_return_high=None,
            post_return_low=None,
            post_return_high=None,
            control_transfer_time_ns=None,
            control_transfer_flow=None,
            entry_retest_detached_time_ns=None,
            entry_retest_time_ns=None,
            committed_geometry=None,
        )

    @staticmethod
    def _await_failed_acceptance_decision(
        state: CampaignSnapshot,
        time_ns: int,
    ) -> CampaignSnapshot:
        """Consume the acceptance retest without inventing another entry.

        A minute close back inside is evidence that acceptance failed, but the
        aggregate decision candle owns the trap conversion.  Until that candle
        completes, neither the old acceptance geometry nor a later refinement
        may create another continuation attempt.
        """

        return replace(
            state,
            phase=CampaignPhase.WAITING_FAILED_ACCEPTANCE_DECISION,
            committed_geometry=None,
            refinement=None,
            first_return_detached_time_ns=None,
            first_return_time_ns=time_ns,
        )

    @staticmethod
    def _confirm_trap_reclaim(state: CampaignSnapshot, time_ns: int) -> CampaignSnapshot:
        if state.reversal is None:
            return ParentCampaignOwner._no_geometry(
                state,
                "trap_reclaim_settled_without_causal_reversal_destination",
            )
        return replace(
            state,
            phase=CampaignPhase.WAITING_REVERSAL_RETEST,
            confirmation_time_ns=time_ns,
            response_flow=FlowLedger(),
            first_return_detached_time_ns=None,
            first_return_time_ns=None,
            first_return_low=None,
            first_return_high=None,
            post_return_low=None,
            post_return_high=None,
            control_transfer_time_ns=None,
            control_transfer_flow=None,
            entry_retest_detached_time_ns=None,
            entry_retest_time_ns=None,
            committed_geometry=state.reversal,
        )

    @staticmethod
    def _no_geometry(state: CampaignSnapshot, reason: str) -> CampaignSnapshot:
        return replace(
            state,
            phase=CampaignPhase.NO_GEOMETRY,
            committed_geometry=None,
            terminal_reason=reason,
        )

    @classmethod
    def _resolve_first_return(
        cls,
        previous: CampaignSnapshot,
        state: CampaignSnapshot,
        obs: CampaignObservation,
        events: list[CampaignEvent],
    ) -> CampaignTransition:
        bar = obs.bar
        refinement = state.refinement
        if (
            refinement is None
            or bar.close_time_ns <= refinement.zone.observed_time_ns
            or not cls._touches_zone(bar, refinement.zone)
        ):
            ended = replace(
                state,
                phase=CampaignPhase.FIRST_RETURN_REJECTED,
                first_return_time_ns=bar.close_time_ns,
                terminal_reason="first_return_had_no_preexisting_executable_refinement",
            )
            events.append(_event(ended, CampaignEventKind.FIRST_RETURN_REJECTED, bar.close_time_ns, ended.terminal_reason))
            return CampaignTransition(previous, ended, tuple(events))
        waiting = replace(
            state,
            phase=CampaignPhase.WAITING_CONTROL_TRANSFER,
            first_return_time_ns=bar.close_time_ns,
            first_return_low=bar.low,
            first_return_high=bar.high,
            post_return_low=bar.low,
            post_return_high=bar.high,
            control_transfer_time_ns=None,
            control_transfer_flow=None,
            entry_retest_time_ns=None,
        )
        return CampaignTransition(previous, waiting, tuple(events))

    @classmethod
    def _commit_control_transfer(
        cls,
        previous: CampaignSnapshot,
        state: CampaignSnapshot,
        bar: Bar,
        control: EpisodeFlowControl,
        events: list[CampaignEvent],
    ) -> CampaignTransition:
        refinement = state.refinement
        if refinement is None:
            raise PolicyError("control transfer requires the locked first-return refinement")
        opportunity, rejection = cls._opportunity(state, bar, refinement, control)
        if opportunity is None:
            ended = replace(
                state,
                phase=CampaignPhase.FIRST_RETURN_REJECTED,
                terminal_reason=rejection,
            )
            events.append(_event(ended, CampaignEventKind.FIRST_RETURN_REJECTED, bar.close_time_ns, rejection))
            return CampaignTransition(previous, ended, tuple(events))
        ended = replace(
            state,
            phase=CampaignPhase.OPPORTUNITY_COMMITTED,
            terminal_reason="immutable_preentry_geometry_committed",
        )
        events.append(
            _event(
                ended,
                CampaignEventKind.OPPORTUNITY_COMMITTED,
                bar.close_time_ns,
                ended.terminal_reason,
                gross_rr=opportunity.gross_rr,
            )
        )
        return CampaignTransition(previous, ended, tuple(events), opportunity)

    @classmethod
    def _opportunity(
        cls,
        state: CampaignSnapshot,
        bar: Bar,
        refinement: EntryRefinement,
        flow: EpisodeFlowControl,
    ) -> tuple[StructuralOpportunity | None, str]:
        side = state.active_side
        if side is None or side != refinement.side:
            return None, "refinement_side_does_not_match_campaign_owner"
        geometry = state.committed_geometry
        if geometry is None:
            return None, "settled_hypothesis_has_no_committed_geometry"
        if state.first_return_time_ns is None:
            return None, "control_transfer_has_no_physical_first_return"
        if state.first_return_detached_time_ns is None:
            return None, "first_return_was_not_preceded_by_full_detachment"
        if state.control_transfer_time_ns is None:
            return None, "opportunity_has_no_completed_control_transfer"
        if state.entry_retest_time_ns is None:
            return None, "opportunity_has_no_executable_entry_retest"
        if not (
            (state.confirmation_time_ns or -1)
            < state.first_return_detached_time_ns
            < state.first_return_time_ns
            < state.control_transfer_time_ns
            <= state.entry_retest_time_ns
            == bar.close_time_ns
        ):
            return None, "opportunity_event_clock_is_not_causally_ordered"
        if state.hypothesis in {
            CampaignHypothesis.REJECTION,
            CampaignHypothesis.TRAP,
        } and not (
            state.entry_retest_detached_time_ns is not None
            and state.control_transfer_time_ns
            <= state.entry_retest_detached_time_ns
            < state.entry_retest_time_ns
        ):
            return None, "entry_retest_was_not_preceded_by_post_transfer_detachment"
        route_clear = (
            state.acceptance_route_clear
            if state.hypothesis is CampaignHypothesis.ACCEPTANCE
            else state.reversal_route_clear
        )
        if not route_clear:
            return None, "route_obstacle_moved_before_committed_destination"
        destination = geometry.destination
        target = geometry.committed_target
        entry = bar.close
        # The footprint can nominate a wider structural stop, but never tighten
        # the invalidation committed by the owning source campaign.
        if side == "LONG":
            stop = min(
                geometry.invalidation_price,
                refinement.structural_stop,
            )
            valid = stop < entry < target
        else:
            stop = max(
                geometry.invalidation_price,
                refinement.structural_stop,
            )
            valid = target < entry < stop
        mainline_channel_acceptance = (
            state.hypothesis is CampaignHypothesis.ACCEPTANCE
            and state.source.timeframe_minutes == 15
            and state.source.kind
            in {
                "ASCENDING_CHANNEL_LOWER",
                "DESCENDING_CHANNEL_UPPER",
            }
        )
        if mainline_channel_acceptance:
            if state.first_return_low is None or state.first_return_high is None:
                return None, "channel_acceptance_lost_first_return_extreme"
            stop = (
                min(
                    stop,
                    state.first_return_low - state.tick_size,
                    bar.low - state.tick_size,
                )
                if side == "LONG"
                else max(
                    stop,
                    state.first_return_high + state.tick_size,
                    bar.high + state.tick_size,
                )
            )
            valid = stop < entry < target if side == "LONG" else target < entry < stop
        if not valid:
            return None, "committed_structure_has_no_executable_entry_stop_target_geometry"
        rr = abs(target - entry) / abs(entry - stop)
        if rr < 1.0 - 1e-12:
            return None, "committed_family_destination_yields_gross_rr_below_one"
        family: OpportunityFamily = (
            "ACCEPTED_AUCTION_CONTINUATION"
            if state.hypothesis is CampaignHypothesis.ACCEPTANCE
            else "FAILED_AUCTION_REVERSAL"
        )
        plan_id = stable_id(
            state.campaign_id,
            family,
            bar.close_time_ns,
            refinement.zone.source_bar_open_time_ns,
            prefix="structural-plan-",
        )
        return (
            StructuralOpportunity(
                episode_id=state.campaign_id,
                plan_id=plan_id,
                symbol=state.source.symbol,
                family=family,
                side=side,
                decision_time_ns=bar.close_time_ns,
                first_return_detached_time_ns=(
                    state.first_return_detached_time_ns
                ),
                first_return_time_ns=state.first_return_time_ns,
                control_transfer_time_ns=state.control_transfer_time_ns,
                entry_retest_detached_time_ns=(
                    state.entry_retest_detached_time_ns
                ),
                entry_retest_time_ns=state.entry_retest_time_ns,
                entry=entry,
                stop=stop,
                target=target,
                source_boundary_id=state.source.boundary_id,
                destination_boundary_id=destination.boundary_id,
                entry_zone=refinement.zone,
                flow_control=flow,
                hypothesis=state.hypothesis,
            ),
            "",
        )

    @staticmethod
    def _flow_control(state: CampaignSnapshot, bar: Bar) -> EpisodeFlowControl | None:
        side = state.active_side
        if side is None or state.response_flow.bars <= 0 or state.episode_flow.total_quote <= 0.0:
            return None
        sign = 1.0 if side == "LONG" else -1.0
        lower, upper = state.source.band_at(state.last_structure_serial)
        boundary = upper if side == "LONG" else lower
        final_progress = sign * (bar.close - boundary)
        if final_progress <= 0.0:
            return None
        cumulative = sign * state.episode_flow.signed_quote
        if side == "LONG":
            aligned_quote = state.episode_flow.buy_aggressor_quote
            adverse_quote = state.episode_flow.sell_aggressor_quote
            adverse_penetration = max(0.0, lower - state.episode_low)
            recovery = bar.close - state.episode_low
            aligned_response = state.response_flow.aligned_buy_initiative
            adverse_episode = state.episode_flow.meaningful_sell_flow
        else:
            aligned_quote = state.episode_flow.sell_aggressor_quote
            adverse_quote = state.episode_flow.buy_aggressor_quote
            adverse_penetration = max(0.0, state.episode_high - upper)
            recovery = state.episode_high - bar.close
            aligned_response = state.response_flow.aligned_sell_initiative
            adverse_episode = state.episode_flow.meaningful_buy_flow
        mechanism: str | None = None
        if aligned_response and cumulative >= 0.0:
            mechanism = "AGGRESSOR_INITIATIVE_CONTROL"
        elif adverse_episode and cumulative < 0.0 and recovery > adverse_penetration:
            mechanism = "OPPOSING_AGGRESSION_ABSORBED"
        elif aligned_response and recovery > adverse_penetration:
            mechanism = "INITIATIVE_AFTER_ABSORPTION"
        if mechanism is None:
            return None
        return EpisodeFlowControl(
            mechanism=mechanism,  # type: ignore[arg-type]
            episode_bars=state.episode_flow.bars,
            response_bars=state.response_flow.bars,
            total_quote=state.episode_flow.total_quote,
            aligned_taker_quote=aligned_quote,
            adverse_taker_quote=adverse_quote,
            cumulative_signed_for_side=cumulative,
            adverse_penetration=adverse_penetration,
            recovery_from_extreme=recovery,
            final_control_progress=final_progress,
        )

    @staticmethod
    def _is_control_transfer_bar(state: CampaignSnapshot, bar: Bar) -> bool:
        """Require renewed local price control and contemporaneous initiative.

        The first return is the pullback reference, never the entry signal.
        A later completed minute must close through that bar's directional
        extreme while its own body and signed flow agree.  Both activity and
        directed flow must be meaningful relative to the causal pre-contact
        baseline; accumulated episode flow cannot substitute for present
        initiative.
        """

        side = state.active_side
        if (
            side is None
            or state.first_return_time_ns is None
            or state.first_return_low is None
            or state.first_return_high is None
            or bar.close_time_ns <= state.first_return_time_ns
        ):
            return False
        signed = bar.signed_quote_flow
        meaningful = (
            bar.quote_volume >= state.flow_baseline.median_quote_volume
            and abs(signed) >= state.flow_baseline.median_abs_signed_flow
            and abs(bar.body) >= state.flow_baseline.median_abs_body
        )
        if not meaningful:
            return False
        if side == "LONG":
            return (
                bar.close > state.first_return_high
                and bar.close > bar.open
                and signed > 0.0
            )
        return (
            bar.close < state.first_return_low
            and bar.close < bar.open
            and signed < 0.0
        )

    @staticmethod
    def _control_transfer_flow(
        state: CampaignSnapshot,
        bar: Bar,
    ) -> EpisodeFlowControl:
        """Describe the transfer without replacing current initiative by a score."""

        side = state.active_side
        if side is None:
            raise PolicyError("control transfer requires a settled directional hypothesis")
        sign = 1.0 if side == "LONG" else -1.0
        lower, upper = state.source.band_at(state.last_structure_serial)
        if side == "LONG":
            aligned_quote = state.episode_flow.buy_aggressor_quote
            adverse_quote = state.episode_flow.sell_aggressor_quote
            adverse_penetration = max(0.0, lower - state.episode_low)
            recovery = bar.close - state.episode_low
            adverse_episode = state.episode_flow.meaningful_sell_flow
        else:
            aligned_quote = state.episode_flow.sell_aggressor_quote
            adverse_quote = state.episode_flow.buy_aggressor_quote
            adverse_penetration = max(0.0, state.episode_high - upper)
            recovery = state.episode_high - bar.close
            adverse_episode = state.episode_flow.meaningful_buy_flow
        return EpisodeFlowControl(
            mechanism=(
                "INITIATIVE_AFTER_ABSORPTION"
                if adverse_episode
                else "AGGRESSOR_INITIATIVE_CONTROL"
            ),
            episode_bars=state.episode_flow.bars,
            response_bars=state.response_flow.bars,
            total_quote=state.episode_flow.total_quote,
            aligned_taker_quote=aligned_quote,
            adverse_taker_quote=adverse_quote,
            cumulative_signed_for_side=sign * state.episode_flow.signed_quote,
            adverse_penetration=adverse_penetration,
            recovery_from_extreme=recovery,
            final_control_progress=sign * (
                bar.close - (upper if side == "LONG" else lower)
            ),
        )

    @staticmethod
    def _validate_refinement(
        state: CampaignSnapshot,
        refinement: EntryRefinement,
        observed_time_ns: int,
    ) -> None:
        if refinement.side != state.active_side:
            raise PolicyError("refinement side differs from active campaign hypothesis")
        if refinement.zone.observed_time_ns < state.interaction_time_ns:
            raise PolicyError("entry refinement must be event-local and observed after ownership")
        if refinement.zone.observed_time_ns > observed_time_ns:
            raise PolicyError("entry refinement is future information")

    @staticmethod
    def _touches_band(bar: Bar, lower: float, upper: float) -> bool:
        return bar.low <= upper and bar.high >= lower

    @staticmethod
    def _touches_zone(bar: Bar, zone: EntryZone) -> bool:
        return bar.low <= zone.upper and bar.high >= zone.lower

    @staticmethod
    def _fully_detached(
        bar: Bar,
        side: Side | None,
        lower: float,
        upper: float,
    ) -> bool:
        """Require a completed bar wholly beyond a zone before its return."""

        if side == "LONG":
            return bar.low > upper
        if side == "SHORT":
            return bar.high < lower
        raise PolicyError("detachment requires an active campaign side")

    @staticmethod
    def _closes_outside(
        bar: Bar,
        side: Side,
        lower: float,
        upper: float,
        *,
        source: LiquidityBoundary | None = None,
    ) -> bool:
        if (
            source is not None
            and source.timeframe_minutes == 15
            and "CHANNEL" in source.kind.upper()
        ):
            # Skilled-auction-control: the decision body must transfer through
            # the mature channel edge.  A bar already opened outside is not a
            # newly observed break and cannot originate this owner.
            return (
                bar.open <= upper and bar.close > upper
                if side == "LONG"
                else bar.open >= lower and bar.close < lower
            )
        return bar.close > upper if side == "LONG" else bar.close < lower

    @staticmethod
    def _opened_and_closed_outside(bar: Bar, side: Side, lower: float, upper: float) -> bool:
        return (bar.open > upper and bar.close > upper) if side == "LONG" else (bar.open < lower and bar.close < lower)

    @staticmethod
    def _reclaim_close(bar: Bar, side: Side, lower: float, upper: float) -> bool:
        return bar.close > upper if side == "LONG" else bar.close < lower

    @classmethod
    def _reclaimed(
        cls,
        state: CampaignSnapshot,
        bar: Bar,
        side: Side,
        lower: float,
        upper: float,
    ) -> bool:
        outward_probe = state.episode_high > upper if state.source.side == "HIGH" else state.episode_low < lower
        return outward_probe and cls._reclaim_close(bar, side, lower, upper)

    @staticmethod
    def _structurally_invalidated(state: CampaignSnapshot, bar: Bar) -> bool:
        geometry = state.committed_geometry
        side = state.active_side
        if geometry is None or side is None:
            return False
        # Once an event-local refinement is locked, its wider structural stop
        # and the parent invalidation form one immutable pre-entry boundary.
        # A wick through that boundary ends the opportunity.  Moving the stop
        # behind an already observed excursion would retroactively preserve an
        # invalidated hypothesis.
        stop = geometry.invalidation_price
        if state.refinement is not None:
            stop = (
                min(stop, state.refinement.structural_stop)
                if side == "LONG"
                else max(stop, state.refinement.structural_stop)
            )
        return bar.low <= stop if side == "LONG" else bar.high >= stop

    @staticmethod
    def _active_destination_spent(state: CampaignSnapshot, obs: CampaignObservation) -> bool:
        geometry = state.committed_geometry
        if geometry is None:
            return False
        acceptance_path = (
            state.hypothesis is CampaignHypothesis.ACCEPTANCE
        )
        reversal_path = state.hypothesis in {
            CampaignHypothesis.REJECTION,
            CampaignHypothesis.TRAP,
        }
        if not acceptance_path and not reversal_path:
            return False
        if acceptance_path:
            if not obs.acceptance_destination_fresh:
                return True
            side = state.outward_side
        else:
            if not obs.reversal_destination_fresh:
                return True
            side = state.reversal_side
        target = geometry.committed_target
        # The target may have been consumed earlier in the still-unsettled
        # episode.  Once selected, looking back only at already observed episode
        # extrema prevents a post-hoc farther-target substitution.
        return state.episode_high >= target if side == "LONG" else state.episode_low <= target


__all__ = [
    "CampaignEvent",
    "CampaignEventKind",
    "CampaignHypothesis",
    "CampaignObservation",
    "CampaignPhase",
    "CampaignSeed",
    "CampaignSnapshot",
    "CampaignTransition",
    "EntryRefinement",
    "EpisodeFlowControl",
    "FlowBaseline",
    "HypothesisGeometry",
    "ParentCampaignOwner",
    "StructuralOpportunity",
]
