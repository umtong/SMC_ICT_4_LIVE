"""Causal, source-generation-bound attack genealogy.

The ledger deliberately stops before entry construction.  It preserves the
physical attack/response history from which an ownership model can reason,
without consuming a source on first touch or collapsing repeated attacks into
independent setups.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Any, Mapping


class AttackLedgerError(ValueError):
    """An observation violates the causal ledger contract."""


class SourceSide(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class OwnerSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def opposite(self) -> "OwnerSide":
        return OwnerSide.SHORT if self is OwnerSide.LONG else OwnerSide.LONG


class CampaignPhase(str, Enum):
    ACTIVE = "ACTIVE"
    CLAIMED = "CLAIMED"
    TERMINAL = "TERMINAL"


class AttackOutcome(str, Enum):
    ACTIVE = "ACTIVE"
    RESPONSE_COMPLETED = "RESPONSE_COMPLETED"


class TerminalReason(str, Enum):
    OBJECTIVE_TOUCHED = "OBJECTIVE_TOUCHED"
    SOURCE_INVALIDATED = "SOURCE_INVALIDATED"
    PARENT_GENERATION_SUPERSEDED = "PARENT_GENERATION_SUPERSEDED"


class EventKind(str, Enum):
    CAMPAIGN_STARTED = "CAMPAIGN_STARTED"
    ATTACK_EXTENDED = "ATTACK_EXTENDED"
    RESPONSE_OBSERVED = "RESPONSE_OBSERVED"
    RESPONSE_COMPLETED = "RESPONSE_COMPLETED"
    REATTACK_APPENDED = "REATTACK_APPENDED"
    CLAIM_UPDATED = "CLAIM_UPDATED"
    CAMPAIGN_TERMINATED = "CAMPAIGN_TERMINATED"
    PHYSICAL_ATTACK_DEDUPED = "PHYSICAL_ATTACK_DEDUPED"
    TOUCH_WITHOUT_FRESH_EXTREME = "TOUCH_WITHOUT_FRESH_EXTREME"


@dataclass(frozen=True, order=True, slots=True)
class SourceKey:
    source_id: str
    generation: int

    def __post_init__(self) -> None:
        if not self.source_id:
            raise AttackLedgerError("source_id cannot be empty")
        if self.generation < 1:
            raise AttackLedgerError("source generation must be positive")

    def as_tuple(self) -> tuple[str, int]:
        return (self.source_id, self.generation)


@dataclass(frozen=True, slots=True)
class SourceSpec:
    key: SourceKey
    side: SourceSide
    tick_size: float
    observed_time_ns: int
    parent_id: str
    parent_generation: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.tick_size) or self.tick_size <= 0.0:
            raise AttackLedgerError("tick_size must be positive and finite")
        if self.observed_time_ns < 0:
            raise AttackLedgerError("observed_time_ns cannot be negative")
        if not self.parent_id:
            raise AttackLedgerError("parent_id cannot be empty")
        if self.parent_generation < 1:
            raise AttackLedgerError("parent generation must be positive")


@dataclass(frozen=True, slots=True)
class AttackRecord:
    ordinal: int
    start_time_ns: int
    end_time_ns: int | None
    extreme: float
    intervening_response_extreme: float | None
    frozen_control: float | None
    outcome: AttackOutcome

    @property
    def start(self) -> int:
        return self.start_time_ns

    @property
    def end(self) -> int | None:
        return self.end_time_ns


@dataclass(frozen=True, slots=True)
class CampaignSnapshot:
    key: SourceKey
    campaign_id: str
    start_time_ns: int
    last_event_time_ns: int
    phase: CampaignPhase
    owner: OwnerSide | None
    attacks: tuple[AttackRecord, ...]
    terminal_reason: TerminalReason | None
    terminal_time_ns: int | None


@dataclass(frozen=True, slots=True)
class StructuralEvent:
    sequence: int
    time_ns: int
    kind: EventKind
    key: SourceKey
    attack_ordinal: int | None
    owner: OwnerSide | None
    detail: str


@dataclass(slots=True)
class _Campaign:
    key: SourceKey
    campaign_id: str
    start_time_ns: int
    last_event_time_ns: int
    phase: CampaignPhase
    owner: OwnerSide | None
    attacks: list[AttackRecord]
    terminal_reason: TerminalReason | None = None
    terminal_time_ns: int | None = None

    def snapshot(self) -> CampaignSnapshot:
        return CampaignSnapshot(
            key=self.key,
            campaign_id=self.campaign_id,
            start_time_ns=self.start_time_ns,
            last_event_time_ns=self.last_event_time_ns,
            phase=self.phase,
            owner=self.owner,
            attacks=tuple(self.attacks),
            terminal_reason=self.terminal_reason,
            terminal_time_ns=self.terminal_time_ns,
        )


def _finite(name: str, value: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise AttackLedgerError(f"{name} must be finite")
    return result


class AttackLedger:
    """Maintain independent attack genealogies for one symbol.

    A source generation can create at most one campaign.  Re-attacks append to
    that campaign, and terminal campaigns never reopen.  No wall-clock expiry,
    directional position lock, scoring, or order-entry behavior exists here.
    """

    STATE_VERSION = 1

    def __init__(self, symbol: str) -> None:
        if not symbol:
            raise AttackLedgerError("symbol cannot be empty")
        self.symbol = symbol
        self._sources: dict[SourceKey, SourceSpec] = {}
        self._parent_generations: dict[str, int] = {}
        self._source_aliases: dict[SourceKey, SourceKey] = {}
        self._campaigns: dict[SourceKey, _Campaign] = {}
        self._retired: dict[SourceKey, tuple[TerminalReason, int]] = {}
        self._physical_aliases: dict[str, str] = {}
        self._physical_observations: dict[str, tuple[SourceKey, int]] = {}
        self._events: list[StructuralEvent] = []
        self._sequence = 0
        self._terminal_owners_by_time: dict[int, set[OwnerSide]] = {}
        self._claims_by_time: dict[int, set[OwnerSide]] = {}

    @property
    def events(self) -> tuple[StructuralEvent, ...]:
        return tuple(self._events)

    @property
    def campaigns(self) -> tuple[CampaignSnapshot, ...]:
        return tuple(self._campaigns[key].snapshot() for key in sorted(self._campaigns))

    def campaign(self, key: SourceKey) -> CampaignSnapshot | None:
        canonical = self._resolve_source(key)
        item = self._campaigns.get(canonical)
        return item.snapshot() if item is not None else None

    def register_source(self, spec: SourceSpec) -> tuple[StructuralEvent, ...]:
        if spec.key in self._sources or spec.key in self._source_aliases:
            raise AttackLedgerError(f"duplicate source generation: {spec.key}")
        current_parent_generation = self._parent_generations.get(spec.parent_id)
        if current_parent_generation is not None and spec.parent_generation < current_parent_generation:
            raise AttackLedgerError("cannot register a source from a superseded parent generation")
        before = len(self._events)
        self.supersede_parent(spec.parent_id, spec.parent_generation, spec.observed_time_ns)
        self._sources[spec.key] = spec
        return tuple(self._events[before:])

    def register_source_alias(self, alias: SourceKey, canonical: SourceKey) -> None:
        canonical = self._resolve_source(canonical)
        if canonical not in self._sources:
            raise AttackLedgerError(f"unknown canonical source: {canonical}")
        if alias in self._sources or alias in self._source_aliases:
            raise AttackLedgerError(f"duplicate source or alias: {alias}")
        self._source_aliases[alias] = canonical

    def register_physical_alias(self, alias_id: str, canonical_id: str) -> None:
        if not alias_id or not canonical_id:
            raise AttackLedgerError("physical attack ids cannot be empty")
        canonical_id = self._resolve_physical(canonical_id)
        if alias_id == canonical_id:
            return
        if alias_id in self._physical_observations:
            raise AttackLedgerError("an observed physical id cannot become an alias")
        existing = self._physical_aliases.get(alias_id)
        if existing is not None and self._resolve_physical(existing) != canonical_id:
            raise AttackLedgerError("physical alias already maps to another attack")
        self._physical_aliases[alias_id] = canonical_id

    def record_touch(
        self,
        key: SourceKey,
        *,
        time_ns: int,
        extreme: float,
        physical_attack_id: str | None = None,
    ) -> tuple[StructuralEvent, ...]:
        """Record a source touch or a directional extension of that attack."""
        key = self._known_source(key)
        spec = self._sources[key]
        extreme = _finite("attack extreme", extreme)
        if time_ns < spec.observed_time_ns:
            raise AttackLedgerError("an attack cannot predate its source")
        before = len(self._events)
        physical_id = None
        if physical_attack_id is not None:
            if not physical_attack_id:
                raise AttackLedgerError("physical_attack_id cannot be empty")
            physical_id = self._resolve_physical(physical_attack_id)
            observed = self._physical_observations.get(physical_id)
            if observed is not None:
                observed_key, ordinal = observed
                self._merge_duplicate_extreme(observed_key, ordinal, time_ns, extreme)
                self._emit(
                    time_ns,
                    EventKind.PHYSICAL_ATTACK_DEDUPED,
                    observed_key,
                    ordinal,
                    self._campaigns[observed_key].owner,
                    physical_id,
                )
                return tuple(self._events[before:])

        if key in self._retired:
            return ()
        campaign = self._campaigns.get(key)
        if campaign is None:
            attack = AttackRecord(1, time_ns, None, extreme, None, None, AttackOutcome.ACTIVE)
            campaign = _Campaign(
                key=key,
                campaign_id=f"{self.symbol}:{key.source_id}:{key.generation}",
                start_time_ns=time_ns,
                last_event_time_ns=time_ns,
                phase=CampaignPhase.ACTIVE,
                owner=None,
                attacks=[attack],
            )
            self._campaigns[key] = campaign
            self._emit(time_ns, EventKind.CAMPAIGN_STARTED, key, 1, None, "SOURCE_FIRST_TOUCH")
        else:
            self._check_time(campaign, time_ns)
            current = campaign.attacks[-1]
            if current.outcome is AttackOutcome.ACTIVE:
                if self._is_more_extreme(spec.side, extreme, current.extreme):
                    campaign.attacks[-1] = replace(current, extreme=extreme)
                    self._emit(
                        time_ns,
                        EventKind.ATTACK_EXTENDED,
                        key,
                        current.ordinal,
                        campaign.owner,
                        "NO_COMPLETED_RESPONSE",
                    )
                campaign.last_event_time_ns = max(campaign.last_event_time_ns, time_ns)
            elif self._is_fresh_reattack(spec, extreme, current.extreme):
                ordinal = current.ordinal + 1
                campaign.attacks.append(
                    AttackRecord(ordinal, time_ns, None, extreme, None, None, AttackOutcome.ACTIVE)
                )
                campaign.last_event_time_ns = time_ns
                self._emit(
                    time_ns,
                    EventKind.REATTACK_APPENDED,
                    key,
                    ordinal,
                    campaign.owner,
                    "FRESH_EXTREME_AFTER_COMPLETED_RESPONSE",
                )
            else:
                campaign.last_event_time_ns = max(campaign.last_event_time_ns, time_ns)
                self._emit(
                    time_ns,
                    EventKind.TOUCH_WITHOUT_FRESH_EXTREME,
                    key,
                    current.ordinal,
                    campaign.owner,
                    "COMPLETED_RESPONSE_BUT_NO_TICK_FRESH_EXTREME",
                )

        if physical_id is not None:
            self._physical_observations[physical_id] = (key, campaign.attacks[-1].ordinal)
        return tuple(self._events[before:])

    def observe_response(
        self,
        key: SourceKey,
        *,
        time_ns: int,
        response_extreme: float,
        completed: bool = False,
        frozen_control: float | None = None,
    ) -> tuple[StructuralEvent, ...]:
        """Accumulate a response and, when complete, freeze its control."""
        key = self._active_key(key)
        campaign = self._campaigns[key]
        self._check_time(campaign, time_ns)
        current = campaign.attacks[-1]
        if current.outcome is AttackOutcome.RESPONSE_COMPLETED:
            raise AttackLedgerError("response is already frozen; a fresh attack must append first")
        response_extreme = _finite("response extreme", response_extreme)
        spec = self._sources[key]
        merged = self._response_extreme(spec.side, current.intervening_response_extreme, response_extreme)
        control = current.frozen_control
        outcome = current.outcome
        end_time = current.end_time_ns
        if completed:
            if frozen_control is None:
                raise AttackLedgerError("completed response requires frozen_control")
            control = _finite("frozen control", frozen_control)
            outcome = AttackOutcome.RESPONSE_COMPLETED
            end_time = time_ns
        elif frozen_control is not None:
            raise AttackLedgerError("control freezes only when the response completes")
        campaign.attacks[-1] = replace(
            current,
            end_time_ns=end_time,
            intervening_response_extreme=merged,
            frozen_control=control,
            outcome=outcome,
        )
        campaign.last_event_time_ns = time_ns
        kind = EventKind.RESPONSE_COMPLETED if completed else EventKind.RESPONSE_OBSERVED
        self._emit(time_ns, kind, key, current.ordinal, campaign.owner, "INTERVENING_CONTROL_FROZEN" if completed else "")
        return (self._events[-1],)

    def claim(self, key: SourceKey, *, time_ns: int, owner: OwnerSide) -> tuple[StructuralEvent, ...]:
        """Update ownership without replacing or consuming the campaign."""
        key = self._active_key(key)
        campaign = self._campaigns[key]
        self._check_time(campaign, time_ns)
        if owner.opposite in self._terminal_owners_by_time.get(time_ns, set()):
            raise AttackLedgerError("opposite ownership cannot be claimed on an old owner's terminal bar")
        campaign.owner = owner
        campaign.phase = CampaignPhase.CLAIMED
        campaign.last_event_time_ns = time_ns
        self._claims_by_time.setdefault(time_ns, set()).add(owner)
        self._emit(time_ns, EventKind.CLAIM_UPDATED, key, campaign.attacks[-1].ordinal, owner, "CAMPAIGN_PRESERVED")
        return (self._events[-1],)

    def objective_touched(self, key: SourceKey, *, time_ns: int) -> tuple[StructuralEvent, ...]:
        return self._terminate(key, time_ns, TerminalReason.OBJECTIVE_TOUCHED)

    def source_invalidated(self, key: SourceKey, *, time_ns: int) -> tuple[StructuralEvent, ...]:
        return self._terminate(key, time_ns, TerminalReason.SOURCE_INVALIDATED)

    def supersede_parent(
        self, parent_id: str, parent_generation: int, time_ns: int
    ) -> tuple[StructuralEvent, ...]:
        if not parent_id or parent_generation < 1 or time_ns < 0:
            raise AttackLedgerError("invalid parent supersession")
        current_generation = self._parent_generations.get(parent_id)
        if current_generation is not None and parent_generation < current_generation:
            raise AttackLedgerError("parent generations cannot move backward")
        before = len(self._events)
        candidates = sorted(
            (
                key
                for key, spec in self._sources.items()
                if spec.parent_id == parent_id and spec.parent_generation < parent_generation
            )
        )
        for key in candidates:
            self._terminate(key, time_ns, TerminalReason.PARENT_GENERATION_SUPERSEDED)
        self._parent_generations[parent_id] = parent_generation
        return tuple(self._events[before:])

    def _terminate(
        self, key: SourceKey, time_ns: int, reason: TerminalReason
    ) -> tuple[StructuralEvent, ...]:
        key = self._known_source(key)
        if time_ns < 0:
            raise AttackLedgerError("terminal time cannot be negative")
        if key in self._retired:
            return ()
        campaign = self._campaigns.get(key)
        if campaign is None:
            self._retired[key] = (reason, time_ns)
            return ()
        self._check_time(campaign, time_ns)
        if campaign.owner is not None and campaign.owner.opposite in self._claims_by_time.get(time_ns, set()):
            raise AttackLedgerError("old owner cannot terminate after an opposite same-bar claim")
        campaign.phase = CampaignPhase.TERMINAL
        campaign.terminal_reason = reason
        campaign.terminal_time_ns = time_ns
        campaign.last_event_time_ns = time_ns
        self._retired[key] = (reason, time_ns)
        if campaign.owner is not None:
            self._terminal_owners_by_time.setdefault(time_ns, set()).add(campaign.owner)
        self._emit(
            time_ns,
            EventKind.CAMPAIGN_TERMINATED,
            key,
            campaign.attacks[-1].ordinal,
            campaign.owner,
            reason.value,
        )
        return (self._events[-1],)

    def _known_source(self, key: SourceKey) -> SourceKey:
        key = self._resolve_source(key)
        if key not in self._sources:
            raise AttackLedgerError(f"unknown source generation: {key}")
        return key

    def _active_key(self, key: SourceKey) -> SourceKey:
        key = self._known_source(key)
        campaign = self._campaigns.get(key)
        if campaign is None or campaign.phase is CampaignPhase.TERMINAL:
            raise AttackLedgerError("source has no active campaign")
        return key

    def _resolve_source(self, key: SourceKey) -> SourceKey:
        seen: set[SourceKey] = set()
        while key in self._source_aliases:
            if key in seen:
                raise AttackLedgerError("source alias cycle")
            seen.add(key)
            key = self._source_aliases[key]
        return key

    def _resolve_physical(self, attack_id: str) -> str:
        seen: set[str] = set()
        while attack_id in self._physical_aliases:
            if attack_id in seen:
                raise AttackLedgerError("physical attack alias cycle")
            seen.add(attack_id)
            attack_id = self._physical_aliases[attack_id]
        return attack_id

    def _check_time(self, campaign: _Campaign, time_ns: int) -> None:
        if time_ns < campaign.last_event_time_ns:
            raise AttackLedgerError("campaign observations must be nondecreasing")

    @staticmethod
    def _is_more_extreme(side: SourceSide, candidate: float, prior: float) -> bool:
        return candidate > prior if side is SourceSide.HIGH else candidate < prior

    @classmethod
    def _is_fresh_reattack(cls, spec: SourceSpec, candidate: float, prior: float) -> bool:
        if spec.side is SourceSide.HIGH:
            return candidate >= prior + spec.tick_size
        return candidate <= prior - spec.tick_size

    @staticmethod
    def _response_extreme(side: SourceSide, prior: float | None, candidate: float) -> float:
        if prior is None:
            return candidate
        return min(prior, candidate) if side is SourceSide.HIGH else max(prior, candidate)

    def _merge_duplicate_extreme(
        self, key: SourceKey, ordinal: int, time_ns: int, extreme: float
    ) -> None:
        campaign = self._campaigns[key]
        spec = self._sources[key]
        record = campaign.attacks[ordinal - 1]
        if self._is_more_extreme(spec.side, extreme, record.extreme):
            campaign.attacks[ordinal - 1] = replace(record, extreme=extreme)
        campaign.last_event_time_ns = max(campaign.last_event_time_ns, time_ns)

    def _emit(
        self,
        time_ns: int,
        kind: EventKind,
        key: SourceKey,
        ordinal: int | None,
        owner: OwnerSide | None,
        detail: str,
    ) -> None:
        self._sequence += 1
        self._events.append(StructuralEvent(self._sequence, time_ns, kind, key, ordinal, owner, detail))

    def export_state(self) -> dict[str, Any]:
        """Return a stable, JSON-serializable checkpoint."""
        return {
            "version": self.STATE_VERSION,
            "symbol": self.symbol,
            "sequence": self._sequence,
            "sources": [self._spec_dict(self._sources[key]) for key in sorted(self._sources)],
            "parent_generations": [
                {"parent_id": parent_id, "generation": generation}
                for parent_id, generation in sorted(self._parent_generations.items())
            ],
            "source_aliases": [
                {"alias": self._key_dict(alias), "canonical": self._key_dict(canonical)}
                for alias, canonical in sorted(self._source_aliases.items())
            ],
            "campaigns": [self._campaign_dict(self._campaigns[key]) for key in sorted(self._campaigns)],
            "retired": [
                {"key": self._key_dict(key), "reason": value[0].value, "time_ns": value[1]}
                for key, value in sorted(self._retired.items())
            ],
            "physical_aliases": [
                {"alias": alias, "canonical": canonical}
                for alias, canonical in sorted(self._physical_aliases.items())
            ],
            "physical_observations": [
                {"physical_id": physical_id, "key": self._key_dict(value[0]), "ordinal": value[1]}
                for physical_id, value in sorted(self._physical_observations.items())
            ],
            "events": [self._event_dict(event) for event in self._events],
            "terminal_owners_by_time": self._owners_by_time_dict(self._terminal_owners_by_time),
            "claims_by_time": self._owners_by_time_dict(self._claims_by_time),
        }

    @classmethod
    def restore_state(cls, payload: Mapping[str, Any]) -> "AttackLedger":
        if int(payload.get("version", -1)) != cls.STATE_VERSION:
            raise AttackLedgerError("unsupported attack ledger state version")
        ledger = cls(str(payload["symbol"]))
        for raw in payload.get("sources", []):
            spec = cls._spec_from_dict(raw)
            ledger._sources[spec.key] = spec
        ledger._parent_generations = {
            str(raw["parent_id"]): int(raw["generation"])
            for raw in payload.get("parent_generations", [])
        }
        if not ledger._parent_generations:
            for spec in ledger._sources.values():
                ledger._parent_generations[spec.parent_id] = max(
                    spec.parent_generation,
                    ledger._parent_generations.get(spec.parent_id, -1),
                )
        for raw in payload.get("source_aliases", []):
            ledger._source_aliases[cls._key_from_dict(raw["alias"])] = cls._key_from_dict(raw["canonical"])
        for raw in payload.get("campaigns", []):
            campaign = cls._campaign_from_dict(raw)
            ledger._campaigns[campaign.key] = campaign
        for raw in payload.get("retired", []):
            ledger._retired[cls._key_from_dict(raw["key"])] = (
                TerminalReason(raw["reason"]),
                int(raw["time_ns"]),
            )
        ledger._physical_aliases = {
            str(raw["alias"]): str(raw["canonical"]) for raw in payload.get("physical_aliases", [])
        }
        ledger._physical_observations = {
            str(raw["physical_id"]): (cls._key_from_dict(raw["key"]), int(raw["ordinal"]))
            for raw in payload.get("physical_observations", [])
        }
        ledger._events = [cls._event_from_dict(raw) for raw in payload.get("events", [])]
        ledger._sequence = int(payload.get("sequence", len(ledger._events)))
        ledger._terminal_owners_by_time = cls._owners_by_time_from_dict(
            payload.get("terminal_owners_by_time", [])
        )
        ledger._claims_by_time = cls._owners_by_time_from_dict(payload.get("claims_by_time", []))
        return ledger

    @staticmethod
    def _key_dict(key: SourceKey) -> dict[str, Any]:
        return {"source_id": key.source_id, "generation": key.generation}

    @staticmethod
    def _key_from_dict(raw: Mapping[str, Any]) -> SourceKey:
        return SourceKey(str(raw["source_id"]), int(raw["generation"]))

    @classmethod
    def _spec_dict(cls, spec: SourceSpec) -> dict[str, Any]:
        return {
            "key": cls._key_dict(spec.key),
            "side": spec.side.value,
            "tick_size": spec.tick_size,
            "observed_time_ns": spec.observed_time_ns,
            "parent_id": spec.parent_id,
            "parent_generation": spec.parent_generation,
        }

    @classmethod
    def _spec_from_dict(cls, raw: Mapping[str, Any]) -> SourceSpec:
        return SourceSpec(
            key=cls._key_from_dict(raw["key"]),
            side=SourceSide(raw["side"]),
            tick_size=float(raw["tick_size"]),
            observed_time_ns=int(raw["observed_time_ns"]),
            parent_id=str(raw["parent_id"]),
            parent_generation=int(raw["parent_generation"]),
        )

    @staticmethod
    def _attack_dict(attack: AttackRecord) -> dict[str, Any]:
        return {
            "ordinal": attack.ordinal,
            "start_time_ns": attack.start_time_ns,
            "end_time_ns": attack.end_time_ns,
            "extreme": attack.extreme,
            "intervening_response_extreme": attack.intervening_response_extreme,
            "frozen_control": attack.frozen_control,
            "outcome": attack.outcome.value,
        }

    @staticmethod
    def _attack_from_dict(raw: Mapping[str, Any]) -> AttackRecord:
        return AttackRecord(
            ordinal=int(raw["ordinal"]),
            start_time_ns=int(raw["start_time_ns"]),
            end_time_ns=None if raw["end_time_ns"] is None else int(raw["end_time_ns"]),
            extreme=float(raw["extreme"]),
            intervening_response_extreme=(
                None
                if raw["intervening_response_extreme"] is None
                else float(raw["intervening_response_extreme"])
            ),
            frozen_control=None if raw["frozen_control"] is None else float(raw["frozen_control"]),
            outcome=AttackOutcome(raw["outcome"]),
        )

    @classmethod
    def _campaign_dict(cls, campaign: _Campaign) -> dict[str, Any]:
        return {
            "key": cls._key_dict(campaign.key),
            "campaign_id": campaign.campaign_id,
            "start_time_ns": campaign.start_time_ns,
            "last_event_time_ns": campaign.last_event_time_ns,
            "phase": campaign.phase.value,
            "owner": None if campaign.owner is None else campaign.owner.value,
            "attacks": [cls._attack_dict(item) for item in campaign.attacks],
            "terminal_reason": (
                None if campaign.terminal_reason is None else campaign.terminal_reason.value
            ),
            "terminal_time_ns": campaign.terminal_time_ns,
        }

    @classmethod
    def _campaign_from_dict(cls, raw: Mapping[str, Any]) -> _Campaign:
        return _Campaign(
            key=cls._key_from_dict(raw["key"]),
            campaign_id=str(raw["campaign_id"]),
            start_time_ns=int(raw["start_time_ns"]),
            last_event_time_ns=int(raw["last_event_time_ns"]),
            phase=CampaignPhase(raw["phase"]),
            owner=None if raw["owner"] is None else OwnerSide(raw["owner"]),
            attacks=[cls._attack_from_dict(item) for item in raw["attacks"]],
            terminal_reason=(
                None if raw["terminal_reason"] is None else TerminalReason(raw["terminal_reason"])
            ),
            terminal_time_ns=(
                None if raw["terminal_time_ns"] is None else int(raw["terminal_time_ns"])
            ),
        )

    @classmethod
    def _event_dict(cls, event: StructuralEvent) -> dict[str, Any]:
        return {
            "sequence": event.sequence,
            "time_ns": event.time_ns,
            "kind": event.kind.value,
            "key": cls._key_dict(event.key),
            "attack_ordinal": event.attack_ordinal,
            "owner": None if event.owner is None else event.owner.value,
            "detail": event.detail,
        }

    @classmethod
    def _event_from_dict(cls, raw: Mapping[str, Any]) -> StructuralEvent:
        return StructuralEvent(
            sequence=int(raw["sequence"]),
            time_ns=int(raw["time_ns"]),
            kind=EventKind(raw["kind"]),
            key=cls._key_from_dict(raw["key"]),
            attack_ordinal=(
                None if raw["attack_ordinal"] is None else int(raw["attack_ordinal"])
            ),
            owner=None if raw["owner"] is None else OwnerSide(raw["owner"]),
            detail=str(raw["detail"]),
        )

    @staticmethod
    def _owners_by_time_dict(values: Mapping[int, set[OwnerSide]]) -> list[dict[str, Any]]:
        return [
            {"time_ns": time_ns, "owners": sorted(owner.value for owner in owners)}
            for time_ns, owners in sorted(values.items())
        ]

    @staticmethod
    def _owners_by_time_from_dict(values: Any) -> dict[int, set[OwnerSide]]:
        return {
            int(raw["time_ns"]): {OwnerSide(owner) for owner in raw["owners"]} for raw in values
        }


__all__ = [
    "AttackLedger",
    "AttackLedgerError",
    "AttackOutcome",
    "AttackRecord",
    "CampaignPhase",
    "CampaignSnapshot",
    "EventKind",
    "OwnerSide",
    "SourceKey",
    "SourceSide",
    "SourceSpec",
    "StructuralEvent",
    "TerminalReason",
]
