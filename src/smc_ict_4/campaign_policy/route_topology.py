"""Causal execution topology for one exact source-bound campaign.

This module deliberately does not decide who owns the auction.  It receives an
ownership decision from the campaign model and translates that decision into
one event-local structural order opportunity.  Rejection and acceptance both
wait for an exact event confirmation, then freeze the first source-touching
engulfing OB or directional FVG formed afterward.  Price must distinctly
leave that zone before its first later return can be judged.
The completed first-return response fixes the decision price at its close;
execution is only eligible from the following bar.

An outside close is only an observation.  Acceptance additionally requires a
bar wholly separated from the source, an outside counter-swing, and a later
bar which protects that swing.  The first return then either produces a later
response or terminates; a more convenient later retest is never selected.

All input bars are completed bars.  There is no clock expiry, score, flow gate,
confidence threshold, or position-management behavior here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from .attack_ledger import (
    AttackOutcome,
    AttackRecord,
    CampaignPhase,
    CampaignSnapshot,
    OwnerSide,
    SourceKey,
    SourceSide,
)
from .liquidity_graph import SourceIdentity


class RouteTopologyError(ValueError):
    """Input violates the structural route contract."""


class RouteMode(str, Enum):
    DIRECT_RELEASE = "DIRECT_RELEASE"
    FIRST_DEFENDED_RETURN = "FIRST_DEFENDED_RETURN"


class ZoneKind(str, Enum):
    ORDER_BLOCK = "ORDER_BLOCK"
    FVG = "FVG"


class RoutePhase(str, Enum):
    OBSERVING = "OBSERVING"
    REJECTION_RECLAIMED = "REJECTION_RECLAIMED"
    ACCEPTANCE_OUTSIDE = "ACCEPTANCE_OUTSIDE"
    ACCEPTANCE_SEPARATED = "ACCEPTANCE_SEPARATED"
    ACCEPTANCE_COUNTER_SWING = "ACCEPTANCE_COUNTER_SWING"
    ACCEPTANCE_PROTECTED = "ACCEPTANCE_PROTECTED"
    CLAIM_ZONE_FROZEN = "CLAIM_ZONE_FROZEN"
    CLAIM_ZONE_DEPARTED = "CLAIM_ZONE_DEPARTED"
    FIRST_RETURN_TOUCHED = "FIRST_RETURN_TOUCHED"
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    OPPORTUNITY = "OPPORTUNITY"
    TERMINAL = "TERMINAL"


class RouteEventKind(str, Enum):
    REJECTION_RECLAIMED = "REJECTION_RECLAIMED"
    REJECTION_CONTROL_BROKEN = "REJECTION_CONTROL_BROKEN"
    OUTSIDE_CLOSE_OBSERVED = "OUTSIDE_CLOSE_OBSERVED"
    DISTINCT_SEPARATION_OBSERVED = "DISTINCT_SEPARATION_OBSERVED"
    OUTSIDE_COUNTER_SWING_OBSERVED = "OUTSIDE_COUNTER_SWING_OBSERVED"
    OUTSIDE_COUNTER_SWING_PROTECTED = "OUTSIDE_COUNTER_SWING_PROTECTED"
    CLAIM_ZONE_FROZEN = "CLAIM_ZONE_FROZEN"
    CLAIM_ZONE_DEPARTED = "CLAIM_ZONE_DEPARTED"
    FIRST_RETURN_TOUCHED = "FIRST_RETURN_TOUCHED"
    FIRST_RETURN_FAILED = "FIRST_RETURN_FAILED"
    DIRECT_ROUTE_SELECTED = "DIRECT_ROUTE_SELECTED"
    FIRST_RETURN_ROUTE_SELECTED = "FIRST_RETURN_ROUTE_SELECTED"
    GROSS_RR_REJECTED = "GROSS_RR_REJECTED"
    TERMINATED = "TERMINATED"


def _finite(name: str, value: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RouteTopologyError(f"{name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class CompletedRouteBar:
    open_time_ns: int
    close_time_ns: int
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if self.open_time_ns < 0 or self.close_time_ns <= self.open_time_ns:
            raise RouteTopologyError("bar must have a positive completed interval")
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise RouteTopologyError("inconsistent completed OHLC bar")
        if self.high < self.low:
            raise RouteTopologyError("bar high cannot be below low")


@dataclass(frozen=True, slots=True)
class SourceBand:
    key: SourceKey
    side: SourceSide
    lower: float
    upper: float
    tick_size: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "lower", _finite("source lower", self.lower))
        object.__setattr__(self, "upper", _finite("source upper", self.upper))
        object.__setattr__(self, "tick_size", _finite("tick size", self.tick_size))
        if self.lower >= self.upper:
            raise RouteTopologyError("source band must have positive width")
        if self.tick_size <= 0.0:
            raise RouteTopologyError("tick size must be positive")


@dataclass(frozen=True, slots=True)
class PriceZone:
    lower: float
    upper: float
    origin_time_ns: int
    fvg_lower: float | None = None
    fvg_upper: float | None = None
    kind: ZoneKind | None = None
    invalidation: float | None = None
    observed_time_ns: int | None = None
    formation_time_ns: tuple[int, ...] = ()
    strength_ratio: float | None = None

    def __post_init__(self) -> None:
        for name in ("lower", "upper"):
            object.__setattr__(self, name, _finite(f"zone {name}", getattr(self, name)))
        if self.origin_time_ns < 0:
            raise RouteTopologyError("zone origin time cannot be negative")
        if self.lower > self.upper:
            raise RouteTopologyError("route zone is inverted")
        if (self.fvg_lower is None) != (self.fvg_upper is None):
            raise RouteTopologyError("both FVG bounds must be supplied together")
        if self.fvg_lower is not None:
            object.__setattr__(self, "fvg_lower", _finite("FVG lower", self.fvg_lower))
            object.__setattr__(self, "fvg_upper", _finite("FVG upper", self.fvg_upper))
            assert self.fvg_upper is not None
            if self.fvg_lower > self.fvg_upper:
                raise RouteTopologyError("FVG bounds are inverted")
        if self.invalidation is not None:
            object.__setattr__(self, "invalidation", _finite("zone invalidation", self.invalidation))
        if self.observed_time_ns is not None and self.observed_time_ns < self.origin_time_ns:
            raise RouteTopologyError("zone observation cannot precede its origin")
        if self.strength_ratio is not None:
            object.__setattr__(self, "strength_ratio", _finite("zone strength", self.strength_ratio))
            if self.strength_ratio < 0.0:
                raise RouteTopologyError("zone strength cannot be negative")


@dataclass(frozen=True, slots=True)
class RouteOpportunity:
    source_key: SourceKey
    attack_ordinal: int
    target_identity: SourceIdentity
    owner_side: OwnerSide
    mode: RouteMode
    decision: int
    entry: float
    stop: float
    target: float
    zone: PriceZone
    invalidation: float

    def __post_init__(self) -> None:
        if self.attack_ordinal < 1:
            raise RouteTopologyError("attack ordinal must be positive")
        for name in ("entry", "stop", "target", "invalidation"):
            _finite(name, getattr(self, name))
        if self.owner_side is OwnerSide.LONG:
            ordered = self.stop < self.entry < self.target
        else:
            ordered = self.target < self.entry < self.stop
        if not ordered:
            raise RouteTopologyError("opportunity geometry is not ordered in owner direction")
        if self.stop != self.invalidation:
            raise RouteTopologyError("stop must be the declared structural invalidation")

    @property
    def gross_rr(self) -> float:
        return abs(self.target - self.entry) / abs(self.entry - self.stop)


@dataclass(frozen=True, slots=True)
class RouteEntrySignal:
    """Target-free first-return response fixed by route topology.

    The destination is deliberately absent.  The graph owner binds the first
    causally available objective from its signal-time snapshot exactly once.
    """

    source_key: SourceKey
    attack_ordinal: int
    owner_side: OwnerSide
    mode: RouteMode
    decision: int
    entry: float
    stop: float
    zone: PriceZone
    invalidation: float

    def __post_init__(self) -> None:
        if self.attack_ordinal < 1:
            raise RouteTopologyError("attack ordinal must be positive")
        for name in ("entry", "stop", "invalidation"):
            _finite(name, getattr(self, name))
        ordered = self.stop < self.entry if self.owner_side is OwnerSide.LONG else self.entry < self.stop
        if not ordered:
            raise RouteTopologyError("entry signal stop is not behind entry")
        if self.stop != self.invalidation:
            raise RouteTopologyError("stop must be the declared structural invalidation")


@dataclass(frozen=True, slots=True)
class RouteStructuralEvent:
    decision: int
    kind: RouteEventKind
    source_key: SourceKey
    phase: RoutePhase
    detail: str = ""


RouteOutput = RouteEntrySignal | RouteStructuralEvent


class SourceRouteTopology:
    """Translate an externally owned campaign into one structural order path."""

    def __init__(
        self,
        source: SourceBand,
        *,
        attack_ordinal: int,
    ) -> None:
        if attack_ordinal < 1:
            raise RouteTopologyError("attack ordinal must be positive")
        self.source = source
        self.attack_ordinal = int(attack_ordinal)
        self.target_identity: SourceIdentity | None = None
        self.target: float | None = None
        self.phase = RoutePhase.OBSERVING
        self._bars: list[CompletedRouteBar] = []
        self._last_close_time_ns = -1
        self._owner: OwnerSide | None = None
        self._owner_first_seen_ns: int | None = None
        self._reclaim_time_ns: int | None = None
        self._outside_time_ns: int | None = None
        self._separation_time_ns: int | None = None
        self._counter_bar: CompletedRouteBar | None = None
        self._protected_time_ns: int | None = None
        self._claim_confirmation_time_ns: int | None = None
        self._frozen_claim_zone: PriceZone | None = None
        self._claim_zone_frozen_time_ns: int | None = None
        self._claim_zone_departed_time_ns: int | None = None
        self._entry_signal: RouteEntrySignal | None = None
        self._opportunity: RouteOpportunity | None = None

    @property
    def opportunity(self) -> RouteOpportunity | None:
        return self._opportunity

    @property
    def entry_signal(self) -> RouteEntrySignal | None:
        return self._entry_signal

    @property
    def terminal(self) -> bool:
        return self.phase is RoutePhase.TERMINAL

    def terminate(self, *, decision: int, reason: str) -> tuple[RouteStructuralEvent, ...]:
        """Receive target/source invalidation or supersession from its owner."""
        if decision < self._last_close_time_ns:
            raise RouteTopologyError("terminal event cannot move backward")
        if self.phase is RoutePhase.TERMINAL:
            return ()
        self.phase = RoutePhase.TERMINAL
        return (self._event(decision, RouteEventKind.TERMINATED, reason),)

    def on_bar(
        self,
        bar: CompletedRouteBar,
        *,
        campaign: CampaignSnapshot,
        owner: OwnerSide | None,
    ) -> tuple[RouteOutput, ...]:
        if campaign.key != self.source.key:
            raise RouteTopologyError("campaign and source generation differ")
        if bar.close_time_ns <= self._last_close_time_ns:
            raise RouteTopologyError("completed bars must be strictly increasing")
        self._last_close_time_ns = bar.close_time_ns
        self._bars.append(bar)
        self._validate_campaign_as_of(campaign, bar.close_time_ns)
        if self.phase is RoutePhase.TERMINAL:
            return ()
        if campaign.phase is CampaignPhase.TERMINAL:
            self.phase = RoutePhase.TERMINAL
            return (
                self._event(
                    bar.close_time_ns,
                    RouteEventKind.TERMINATED,
                    campaign.terminal_reason.value if campaign.terminal_reason else "CAMPAIGN_TERMINAL",
                ),
            )
        latest = campaign.attacks[-1] if campaign.attacks else None
        if latest is None or latest.ordinal < self.attack_ordinal:
            raise RouteTopologyError("bound attack ordinal is not present in campaign snapshot")
        if latest.ordinal > self.attack_ordinal:
            self.phase = RoutePhase.TERMINAL
            return (
                self._event(
                    bar.close_time_ns,
                    RouteEventKind.TERMINATED,
                    f"REATTACK_REPLACED_ROUTE:{latest.ordinal}",
                ),
            )
        if self.phase in {RoutePhase.ENTRY_SIGNAL, RoutePhase.OPPORTUNITY}:
            return ()
        if owner is None:
            return ()
        if campaign.owner is not None and campaign.owner is not owner:
            self.phase = RoutePhase.TERMINAL
            return (
                self._event(
                    bar.close_time_ns,
                    RouteEventKind.TERMINATED,
                    f"CAMPAIGN_CLAIMED_BY:{campaign.owner.value}",
                ),
            )
        if self._owner is None:
            self._owner = owner
            self._owner_first_seen_ns = latest.start_time_ns
        elif owner is not self._owner:
            # A source route cannot flip hands in place.  The ledger must first
            # retire this source generation and create/claim a later campaign.
            self.phase = RoutePhase.TERMINAL
            return (
                self._event(bar.close_time_ns, RouteEventKind.TERMINATED, "OWNER_CHANGED"),
            )

        if self._is_rejection_owner(owner):
            return self._advance_rejection(bar, campaign, owner)
        return self._advance_acceptance(bar, campaign, owner)

    def _advance_rejection(
        self,
        bar: CompletedRouteBar,
        campaign: CampaignSnapshot,
        owner: OwnerSide,
    ) -> tuple[RouteOutput, ...]:
        latest = campaign.attacks[self.attack_ordinal - 1]
        if not self._attack_excursed(latest):
            return ()
        if self._claim_zone_frozen_time_ns is not None:
            return self._advance_claim_zone_return(bar, campaign, owner)
        if self._claim_confirmation_time_ns is not None:
            return self._observe_event_local_zone(bar, owner)
        output: list[RouteOutput] = []
        if self._reclaim_time_ns is None and self._reclaimed(bar):
            self._reclaim_time_ns = bar.close_time_ns
            self.phase = RoutePhase.REJECTION_RECLAIMED
            output.append(self._event(bar.close_time_ns, RouteEventKind.REJECTION_RECLAIMED))

        if (
            self._reclaim_time_ns is None
            or latest.outcome is not AttackOutcome.RESPONSE_COMPLETED
            or latest.frozen_control is None
            or latest.end_time_ns is None
        ):
            return tuple(output)
        causal_floor = max(
            self._reclaim_time_ns,
            latest.end_time_ns,
            self._owner_first_seen_ns or -1,
        )
        if bar.close_time_ns <= causal_floor:
            return tuple(output)
        broke = (
            bar.close < latest.frozen_control
            if owner is OwnerSide.SHORT
            else bar.close > latest.frozen_control
        )
        if not broke:
            return tuple(output)
        self._claim_confirmation_time_ns = bar.close_time_ns
        output.append(self._event(bar.close_time_ns, RouteEventKind.REJECTION_CONTROL_BROKEN))
        return tuple(output)

    def _advance_acceptance(
        self,
        bar: CompletedRouteBar,
        campaign: CampaignSnapshot,
        owner: OwnerSide,
    ) -> tuple[RouteOutput, ...]:
        output: list[RouteOutput] = []
        attack = campaign.attacks[self.attack_ordinal - 1]
        if not self._attack_excursed(attack):
            return ()
        if self._claim_zone_frozen_time_ns is not None:
            return self._advance_claim_zone_return(bar, campaign, owner)
        if self._claim_confirmation_time_ns is not None:
            return self._observe_event_local_zone(bar, owner)
        if self._outside_time_ns is None:
            if self._outside_close(bar):
                self._outside_time_ns = bar.close_time_ns
                self.phase = RoutePhase.ACCEPTANCE_OUTSIDE
                output.append(self._event(bar.close_time_ns, RouteEventKind.OUTSIDE_CLOSE_OBSERVED))
            return tuple(output)

        if self._separation_time_ns is None:
            if bar.close_time_ns > self._outside_time_ns and self._separated(bar):
                self._separation_time_ns = bar.close_time_ns
                self.phase = RoutePhase.ACCEPTANCE_SEPARATED
                output.append(
                    self._event(bar.close_time_ns, RouteEventKind.DISTINCT_SEPARATION_OBSERVED)
                )
            return tuple(output)

        if self._counter_bar is None:
            if self._touches_source(bar):
                self.phase = RoutePhase.TERMINAL
                output.append(
                    self._event(
                        bar.close_time_ns,
                        RouteEventKind.TERMINATED,
                        "SEPARATION_RETURNED_BEFORE_COUNTER_SWING",
                    )
                )
                return tuple(output)
            if bar.close_time_ns > self._separation_time_ns and self._is_counter_bar(bar, owner):
                self._counter_bar = bar
                self.phase = RoutePhase.ACCEPTANCE_COUNTER_SWING
                output.append(
                    self._event(bar.close_time_ns, RouteEventKind.OUTSIDE_COUNTER_SWING_OBSERVED)
                )
            return tuple(output)

        if self._protected_time_ns is None:
            if self._touches_source(bar) or self._counter_invalidated(bar, owner):
                self.phase = RoutePhase.TERMINAL
                output.append(
                    self._event(
                        bar.close_time_ns,
                        RouteEventKind.TERMINATED,
                        "COUNTER_SWING_NOT_PROTECTED",
                    )
                )
                return tuple(output)
            if bar.close_time_ns > self._counter_bar.close_time_ns and self._protects_counter(bar, owner):
                self._protected_time_ns = bar.close_time_ns
                self.phase = RoutePhase.ACCEPTANCE_PROTECTED
                self._claim_confirmation_time_ns = bar.close_time_ns
                output.append(
                    self._event(bar.close_time_ns, RouteEventKind.OUTSIDE_COUNTER_SWING_PROTECTED)
                )
            return tuple(output)

        return self._advance_claim_zone_return(bar, campaign, owner)

    def _observe_event_local_zone(
        self,
        bar: CompletedRouteBar,
        owner: OwnerSide,
    ) -> tuple[RouteStructuralEvent, ...]:
        confirmation = self._claim_confirmation_time_ns
        assert confirmation is not None
        if bar.close_time_ns <= confirmation:
            return ()
        zone = self._detect_event_local_zone(owner, confirmation)
        if zone is None:
            return ()
        self._frozen_claim_zone = zone
        self._claim_zone_frozen_time_ns = bar.close_time_ns
        self.phase = RoutePhase.CLAIM_ZONE_FROZEN
        return (self._event(bar.close_time_ns, RouteEventKind.CLAIM_ZONE_FROZEN),)

    def _advance_claim_zone_return(
        self,
        bar: CompletedRouteBar,
        campaign: CampaignSnapshot,
        owner: OwnerSide,
    ) -> tuple[RouteOutput, ...]:
        """Require departure, the first later zone retest, and its response."""
        zone = self._frozen_claim_zone
        frozen_at = self._claim_zone_frozen_time_ns
        assert zone is not None and frozen_at is not None
        if bar.close_time_ns <= frozen_at:
            return ()
        if self._hits_structural_stop(bar, campaign, owner):
            return self._fail_first_return(bar.close_time_ns, "STRUCTURAL_INVALIDATION")

        if self._claim_zone_departed_time_ns is None:
            departed = bar.close > zone.upper if owner is OwnerSide.LONG else bar.close < zone.lower
            if not departed:
                return ()
            self._claim_zone_departed_time_ns = bar.close_time_ns
            self.phase = RoutePhase.CLAIM_ZONE_DEPARTED
            return (self._event(bar.close_time_ns, RouteEventKind.CLAIM_ZONE_DEPARTED),)

        if bar.close_time_ns <= self._claim_zone_departed_time_ns:
            return ()
        if not self._touches_zone(bar, zone):
            return ()
        self.phase = RoutePhase.FIRST_RETURN_TOUCHED
        output: list[RouteOutput] = [
            self._event(bar.close_time_ns, RouteEventKind.FIRST_RETURN_TOUCHED)
        ]
        if self._hits_structural_stop(bar, campaign, owner):
            output.extend(
                self._fail_first_return(bar.close_time_ns, "STOP_TOUCHED_ON_FIRST_RETURN")
            )
            return tuple(output)
        reacted = (
            bar.close > zone.upper and bar.close > bar.open
            if owner is OwnerSide.LONG
            else bar.close < zone.lower and bar.close < bar.open
        )
        if not reacted:
            output.extend(
                self._fail_first_return(bar.close_time_ns, "FIRST_RETURN_NOT_DEFENDED")
            )
            return tuple(output)
        signal = self._build_entry_signal(
            bar=bar,
            campaign=campaign,
            owner=owner,
            mode=RouteMode.FIRST_DEFENDED_RETURN,
        )
        output.extend(self._finish_signal(
            signal,
            bar.close_time_ns,
            RouteEventKind.FIRST_RETURN_ROUTE_SELECTED,
        ))
        return tuple(output)

    def _finish_signal(
        self,
        signal: RouteEntrySignal,
        decision: int,
        selected_kind: RouteEventKind,
    ) -> tuple[RouteOutput, ...]:
        self._entry_signal = signal
        self.phase = RoutePhase.ENTRY_SIGNAL
        return (self._event(decision, selected_kind, signal.mode.value), signal)

    def bind_target(
        self,
        signal: RouteEntrySignal,
        *,
        target_identity: SourceIdentity,
        target: float,
    ) -> RouteOpportunity | None:
        """Bind the one signal-time objective; a rejected bind is terminal."""

        if self.phase is not RoutePhase.ENTRY_SIGNAL or signal is not self._entry_signal:
            raise RouteTopologyError("target can only bind the exact unbound entry signal")
        if self.target_identity is not None or self.target is not None:
            raise RouteTopologyError("route target is already bound")
        target_value = _finite("target", target)
        valid = (
            signal.stop < signal.entry < target_value
            if signal.owner_side is OwnerSide.LONG
            else target_value < signal.entry < signal.stop
        )
        rr = (
            abs(target_value - signal.entry) / abs(signal.entry - signal.stop)
            if valid
            else 0.0
        )
        if not valid or rr < 1.0:
            self.phase = RoutePhase.TERMINAL
            return None
        self.target_identity = target_identity
        self.target = target_value
        self._opportunity = RouteOpportunity(
            source_key=signal.source_key,
            attack_ordinal=signal.attack_ordinal,
            target_identity=target_identity,
            owner_side=signal.owner_side,
            mode=signal.mode,
            decision=signal.decision,
            entry=signal.entry,
            stop=signal.stop,
            target=target_value,
            zone=signal.zone,
            invalidation=signal.invalidation,
        )
        self.phase = RoutePhase.OPPORTUNITY
        return self._opportunity

    def reject_target(self, *, decision: int, reason: str) -> tuple[RouteStructuralEvent, ...]:
        if self.phase is not RoutePhase.ENTRY_SIGNAL:
            raise RouteTopologyError("only an unbound entry signal can reject a target")
        self.phase = RoutePhase.TERMINAL
        return (self._event(decision, RouteEventKind.GROSS_RR_REJECTED, reason),)

    def _fail_first_return(self, decision: int, detail: str) -> tuple[RouteStructuralEvent, ...]:
        self.phase = RoutePhase.TERMINAL
        return (self._event(decision, RouteEventKind.FIRST_RETURN_FAILED, detail),)

    def _build_entry_signal(
        self,
        *,
        bar: CompletedRouteBar,
        campaign: CampaignSnapshot,
        owner: OwnerSide,
        mode: RouteMode,
    ) -> RouteEntrySignal:
        zone = self._frozen_claim_zone
        if zone is None:
            raise RouteTopologyError("entry signal requires a frozen claim zone")
        stop = self._structural_stop(campaign, owner, response_bar=bar)
        # The completed response close is the decision price.  The execution
        # harness forbids a same-bar fill and applies it from the next bar.
        entry = bar.close
        return RouteEntrySignal(
            source_key=self.source.key,
            attack_ordinal=self.attack_ordinal,
            owner_side=owner,
            mode=mode,
            decision=bar.close_time_ns,
            entry=entry,
            stop=stop,
            zone=zone,
            invalidation=stop,
        )

    def _detect_event_local_zone(
        self,
        owner: OwnerSide,
        confirmation_time_ns: int,
    ) -> PriceZone | None:
        """Return the first confirmed EasyChart OB/FVG after this event."""
        if not self._bars or self._bars[-1].close_time_ns <= confirmation_time_ns:
            return None
        current = self._bars[-1]

        if len(self._bars) >= 2:
            previous = self._bars[-2]
            previous_lower = min(previous.open, previous.close)
            previous_upper = max(previous.open, previous.close)
            current_lower = min(current.open, current.close)
            current_upper = max(current.open, current.close)
            previous_body = previous_upper - previous_lower
            current_body = current_upper - current_lower
            intended = (
                previous.close < previous.open
                and current.close > current.open
                if owner is OwnerSide.LONG
                else previous.close > previous.open and current.close < current.open
            )
            engulfed = current_lower <= previous_lower and current_upper >= previous_upper
            ratio = current_body / max(previous_body, self.source.tick_size)
            formation = (previous, current)
            if (
                intended
                and engulfed
                and previous_body > 0.0
                and current_body > 0.0
                and ratio + 1e-12 >= 2.0
                and self._formation_touches_context(formation)
            ):
                invalidation = (
                    min(item.low for item in formation) - self.source.tick_size
                    if owner is OwnerSide.LONG
                    else max(item.high for item in formation) + self.source.tick_size
                )
                return PriceZone(
                    previous_lower,
                    previous_upper,
                    previous.close_time_ns,
                    kind=ZoneKind.ORDER_BLOCK,
                    invalidation=invalidation,
                    observed_time_ns=current.close_time_ns,
                    formation_time_ns=tuple(item.close_time_ns for item in formation),
                    strength_ratio=ratio,
                )

        if len(self._bars) < 3:
            return None
        first, middle, third = self._bars[-3:]
        first_body = abs(first.close - first.open)
        middle_body = abs(middle.close - middle.open)
        third_body = abs(third.close - third.open)
        ratio = middle_body / max(first_body, third_body, self.source.tick_size)
        bullish = owner is OwnerSide.LONG and first.high < third.low and middle.close > middle.open
        bearish = owner is OwnerSide.SHORT and first.low > third.high and middle.close < middle.open
        formation = (first, middle, third)
        if (
            not (bullish or bearish)
            or ratio + 1e-12 < 2.0
            or not self._formation_touches_context(formation)
        ):
            return None
        if bullish:
            lower, upper = first.high, third.low
            invalidation = min(item.low for item in formation) - self.source.tick_size
        else:
            lower, upper = third.high, first.low
            invalidation = max(item.high for item in formation) + self.source.tick_size
        return PriceZone(
            lower,
            upper,
            middle.close_time_ns,
            fvg_lower=lower,
            fvg_upper=upper,
            kind=ZoneKind.FVG,
            invalidation=invalidation,
            observed_time_ns=third.close_time_ns,
            formation_time_ns=tuple(item.close_time_ns for item in formation),
            strength_ratio=ratio,
        )

    def _formation_touches_context(self, formation: tuple[CompletedRouteBar, ...]) -> bool:
        return any(self._touches_source(item) for item in formation)

    def _structural_stop(
        self,
        campaign: CampaignSnapshot,
        owner: OwnerSide,
        *,
        response_bar: CompletedRouteBar | None = None,
    ) -> float:
        rejection = self._is_rejection_owner(owner)
        attack = campaign.attacks[self.attack_ordinal - 1]
        if owner is OwnerSide.LONG:
            base = self.source.lower
            if rejection:
                base = min(base, attack.extreme)
            elif self._counter_bar is not None:
                base = min(base, self._counter_bar.low)
            candidates = [base - self.source.tick_size]
            if self._frozen_claim_zone is not None and self._frozen_claim_zone.invalidation is not None:
                candidates.append(self._frozen_claim_zone.invalidation)
            if response_bar is not None:
                candidates.append(response_bar.low - self.source.tick_size)
            return min(candidates)
        base = self.source.upper
        if rejection:
            base = max(base, attack.extreme)
        elif self._counter_bar is not None:
            base = max(base, self._counter_bar.high)
        candidates = [base + self.source.tick_size]
        if self._frozen_claim_zone is not None and self._frozen_claim_zone.invalidation is not None:
            candidates.append(self._frozen_claim_zone.invalidation)
        if response_bar is not None:
            candidates.append(response_bar.high + self.source.tick_size)
        return max(candidates)

    def _hits_structural_stop(
        self, bar: CompletedRouteBar, campaign: CampaignSnapshot, owner: OwnerSide
    ) -> bool:
        stop = self._structural_stop(campaign, owner)
        return bar.low <= stop if owner is OwnerSide.LONG else bar.high >= stop

    def _attack_excursed(self, attack: AttackRecord) -> bool:
        extreme = attack.extreme
        if self.source.side is SourceSide.HIGH:
            return extreme > self.source.upper
        return extreme < self.source.lower

    def _is_rejection_owner(self, owner: OwnerSide) -> bool:
        return (self.source.side is SourceSide.HIGH and owner is OwnerSide.SHORT) or (
            self.source.side is SourceSide.LOW and owner is OwnerSide.LONG
        )

    def _outside_close(self, bar: CompletedRouteBar) -> bool:
        return bar.close > self.source.upper if self.source.side is SourceSide.HIGH else bar.close < self.source.lower

    def _reclaimed(self, bar: CompletedRouteBar) -> bool:
        return (
            bar.close < self.source.lower
            if self.source.side is SourceSide.HIGH
            else bar.close > self.source.upper
        )

    @staticmethod
    def _validate_campaign_as_of(campaign: CampaignSnapshot, decision: int) -> None:
        if campaign.start_time_ns > decision or campaign.last_event_time_ns > decision:
            raise RouteTopologyError("campaign snapshot contains future state")
        for attack in campaign.attacks:
            if attack.start_time_ns > decision or (
                attack.end_time_ns is not None and attack.end_time_ns > decision
            ):
                raise RouteTopologyError("campaign attack contains future state")

    def _separated(self, bar: CompletedRouteBar) -> bool:
        return bar.low > self.source.upper if self.source.side is SourceSide.HIGH else bar.high < self.source.lower

    def _touches_source(self, bar: CompletedRouteBar) -> bool:
        return bar.low <= self.source.upper and bar.high >= self.source.lower

    @staticmethod
    def _touches_zone(bar: CompletedRouteBar, zone: PriceZone) -> bool:
        return bar.low <= zone.upper and bar.high >= zone.lower

    def _is_counter_bar(self, bar: CompletedRouteBar, owner: OwnerSide) -> bool:
        if not self._separated(bar):
            return False
        return bar.close < bar.open if owner is OwnerSide.LONG else bar.close > bar.open

    def _counter_invalidated(self, bar: CompletedRouteBar, owner: OwnerSide) -> bool:
        assert self._counter_bar is not None
        return bar.low < self._counter_bar.low if owner is OwnerSide.LONG else bar.high > self._counter_bar.high

    def _protects_counter(self, bar: CompletedRouteBar, owner: OwnerSide) -> bool:
        assert self._counter_bar is not None
        if owner is OwnerSide.LONG:
            return bar.close > self._counter_bar.high and bar.low >= self._counter_bar.low
        return bar.close < self._counter_bar.low and bar.high <= self._counter_bar.high

    def _event(
        self, decision: int, kind: RouteEventKind, detail: str = ""
    ) -> RouteStructuralEvent:
        return RouteStructuralEvent(decision, kind, self.source.key, self.phase, detail)


__all__ = [
    "CompletedRouteBar",
    "PriceZone",
    "RouteEventKind",
    "RouteMode",
    "RouteEntrySignal",
    "RouteOpportunity",
    "RouteOutput",
    "RoutePhase",
    "RouteStructuralEvent",
    "RouteTopologyError",
    "SourceBand",
    "SourceRouteTopology",
    "ZoneKind",
]
