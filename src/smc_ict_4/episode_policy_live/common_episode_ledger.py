"""Shared causal ownership for completed common-market candidates.

The local structural/value FSM owns price interpretation and complete trade
geometry.  This ledger does not replay or second-guess that FSM.  It records
one synchronized broad outside attack, freezes the inventory mechanism that
was observable at that attack, and answers the narrower question: does the
common episode retain causal responsibility for this *completed* candidate?

There are no clocks, fitted magnitudes, scores, or synthetic exits here.  A
root remains live until the caller claims or physically invalidates it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .domain import SYMBOLS, stable_id
from .inventory_ownership import (
    InventoryDecision,
    InventoryInterpretation,
    InventoryRegime,
    OwnershipBranch,
)


class CommonEpisodeError(ValueError):
    """A common episode registration or transition is non-causal."""


class CommonEpisodeFamily(str, Enum):
    CONTINUATION = "COMMON_CASCADE_CONTINUATION"
    REVERSAL = "FAILED_COMMON_CASCADE_REVERSAL"


class CommonEpisodeState(str, Enum):
    OPEN = "OPEN"
    CLAIMED = "CLAIMED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class CommonAttack:
    """Immutable public identity and registration evidence for one attack.

    ``attack_inventory`` is the observation available at registration.  When
    it is unknown, the ledger's effective frozen attack inventory is filled by
    the first strictly-later known observation.
    """

    root_id: str
    sibling_root_id: str
    attack_time_ns: int
    attack_side: str
    participants: tuple[str, ...]
    participant_source_roots: tuple[tuple[str, str], ...]
    attack_inventory: tuple[tuple[str, InventoryDecision], ...]

    def source_roots_for(self, symbol: str) -> tuple[str, ...]:
        roots = tuple(
            source_root
            for participant, source_root in self.participant_source_roots
            if participant == symbol
        )
        if not roots:
            raise CommonEpisodeError(f"{symbol} is not a participant in {self.root_id}")
        return roots

    def source_root_for(
        self,
        symbol: str,
        source_campaign_root_id: str | None = None,
    ) -> str:
        roots = self.source_roots_for(symbol)
        if source_campaign_root_id is not None:
            if source_campaign_root_id not in roots:
                raise CommonEpisodeError(
                    f"{source_campaign_root_id} is not a {symbol} source in {self.root_id}"
                )
            return source_campaign_root_id
        if len(roots) != 1:
            raise CommonEpisodeError(
                f"{symbol} swept multiple sources; the exact native root is required"
            )
        return roots[0]

    def inventory_for(self, symbol: str) -> InventoryDecision:
        for participant, decision in self.attack_inventory:
            if participant == symbol:
                return decision
        raise CommonEpisodeError(f"{symbol} has no frozen attack inventory")


@dataclass(frozen=True, slots=True)
class CommonCandidateAuthorization:
    """CIRB responsibility attached to an already-completed price candidate."""

    authorization_id: str
    root_id: str
    sibling_root_id: str
    source_campaign_root_id: str
    symbol: str
    family: CommonEpisodeFamily
    side: str
    attack_side: str
    attack_time_ns: int
    candidate_time_ns: int
    participants: tuple[str, ...]
    participant_source_roots: tuple[tuple[str, str], ...]
    responsibility: str
    attack_inventory_interpretation: InventoryInterpretation
    attack_inventory_decision_time_ns: int
    attack_inventory_observed_time_ns: int
    latest_inventory_interpretation: InventoryInterpretation
    latest_inventory_regime: InventoryRegime
    latest_inventory_decision_time_ns: int
    latest_inventory_observed_time_ns: int
    first_position_reset_observed_time_ns: int | None
    first_counter_sponsorship_observed_time_ns: int | None

    @property
    def evidence(self) -> Mapping[str, object]:
        return {
            "common_authorization_id": self.authorization_id,
            "common_root_id": self.root_id,
            "common_sibling_root_id": self.sibling_root_id,
            "source_campaign_root_id": self.source_campaign_root_id,
            "common_attack_time_ns": self.attack_time_ns,
            "common_attack_side": self.attack_side,
            "common_participants": self.participants,
            "common_participant_source_roots": self.participant_source_roots,
            "common_inventory_responsibility": self.responsibility,
            "attack_inventory_interpretation": (
                self.attack_inventory_interpretation.value
            ),
            "attack_inventory_decision_time_ns": (
                self.attack_inventory_decision_time_ns
            ),
            "attack_inventory_observed_time_ns": (
                self.attack_inventory_observed_time_ns
            ),
            "latest_inventory_interpretation": (
                self.latest_inventory_interpretation.value
            ),
            "latest_inventory_regime": self.latest_inventory_regime.value,
            "latest_inventory_decision_time_ns": (
                self.latest_inventory_decision_time_ns
            ),
            "latest_inventory_observed_time_ns": (
                self.latest_inventory_observed_time_ns
            ),
            "first_position_reset_observed_time_ns": (
                self.first_position_reset_observed_time_ns
            ),
            "first_counter_sponsorship_observed_time_ns": (
                self.first_counter_sponsorship_observed_time_ns
            ),
        }


@dataclass(slots=True)
class _CommonEpisodeRecord:
    attack: CommonAttack
    frozen_attack_inventory: dict[str, InventoryDecision | None]
    latest_inventory: dict[str, InventoryDecision]
    participant_join_time_ns: dict[str, int]
    latest_official_observed_time_ns: dict[str, int | None]
    first_position_reset_observed_time_ns: dict[str, int | None]
    first_counter_sponsorship_observed_time_ns: dict[str, int | None]
    state: CommonEpisodeState = CommonEpisodeState.OPEN
    active_authorization_ids: set[str] = field(default_factory=set)
    claimed_authorization_id: str | None = None
    terminal_reason: str | None = None


def _side(value: str) -> str:
    side = value.upper()
    if side not in {"LONG", "SHORT"}:
        raise CommonEpisodeError("side must be LONG or SHORT")
    return side


def _opposite(side: str) -> str:
    return "SHORT" if side == "LONG" else "LONG"


def _inventory_side(value: str) -> str:
    """Translate the official inventory shock vocabulary into plan sides."""

    normalized = value.upper()
    try:
        return {
            "BUY": "LONG",
            "SELL": "SHORT",
            "LONG": "LONG",
            "SHORT": "SHORT",
        }[normalized]
    except KeyError as exc:
        raise CommonEpisodeError(
            "inventory shock side must be BUY/SELL or LONG/SHORT"
        ) from exc


def _inventory_to_payload(decision: InventoryDecision) -> dict[str, object]:
    return {
        "symbol": decision.symbol,
        "regime": decision.regime.value,
        "ownership": decision.ownership.value,
        "interpretation": decision.interpretation.value,
        "reason": decision.reason,
        "shock_side": decision.shock_side,
        "episode_start_ns": decision.episode_start_ns,
        "decision_ts_ns": decision.decision_ts_ns,
        "prior_observed_ts_ns": decision.prior_observed_ts_ns,
        "current_observed_ts_ns": decision.current_observed_ts_ns,
        "oi_change_fraction": decision.oi_change_fraction,
        "all_account_change_log": decision.all_account_change_log,
        "price_flow_aligned": decision.price_flow_aligned,
    }


def _inventory_from_payload(raw: Mapping[str, Any]) -> InventoryDecision:
    aligned = raw["price_flow_aligned"]
    if aligned is not None and not isinstance(aligned, bool):
        raise CommonEpisodeError("price_flow_aligned must be bool or null")
    return InventoryDecision(
        symbol=str(raw["symbol"]),
        regime=InventoryRegime(str(raw["regime"])),
        ownership=OwnershipBranch(str(raw["ownership"])),
        interpretation=InventoryInterpretation(str(raw["interpretation"])),
        reason=str(raw["reason"]),
        shock_side=str(raw["shock_side"]),
        episode_start_ns=int(raw["episode_start_ns"]),
        decision_ts_ns=int(raw["decision_ts_ns"]),
        prior_observed_ts_ns=(None if raw["prior_observed_ts_ns"] is None else int(raw["prior_observed_ts_ns"])),
        current_observed_ts_ns=(None if raw["current_observed_ts_ns"] is None else int(raw["current_observed_ts_ns"])),
        oi_change_fraction=(None if raw["oi_change_fraction"] is None else float(raw["oi_change_fraction"])),
        all_account_change_log=(None if raw["all_account_change_log"] is None else float(raw["all_account_change_log"])),
        price_flow_aligned=aligned,
    )


def _authorization_to_payload(value: CommonCandidateAuthorization) -> dict[str, object]:
    return {
        "authorization_id": value.authorization_id,
        "root_id": value.root_id,
        "sibling_root_id": value.sibling_root_id,
        "source_campaign_root_id": value.source_campaign_root_id,
        "symbol": value.symbol,
        "family": value.family.value,
        "side": value.side,
        "attack_side": value.attack_side,
        "attack_time_ns": value.attack_time_ns,
        "candidate_time_ns": value.candidate_time_ns,
        "participants": list(value.participants),
        "participant_source_roots": [list(pair) for pair in value.participant_source_roots],
        "responsibility": value.responsibility,
        "attack_inventory_interpretation": value.attack_inventory_interpretation.value,
        "attack_inventory_decision_time_ns": value.attack_inventory_decision_time_ns,
        "attack_inventory_observed_time_ns": value.attack_inventory_observed_time_ns,
        "latest_inventory_interpretation": value.latest_inventory_interpretation.value,
        "latest_inventory_regime": value.latest_inventory_regime.value,
        "latest_inventory_decision_time_ns": value.latest_inventory_decision_time_ns,
        "latest_inventory_observed_time_ns": value.latest_inventory_observed_time_ns,
        "first_position_reset_observed_time_ns": value.first_position_reset_observed_time_ns,
        "first_counter_sponsorship_observed_time_ns": value.first_counter_sponsorship_observed_time_ns,
    }


def _authorization_from_payload(raw: Mapping[str, Any]) -> CommonCandidateAuthorization:
    return CommonCandidateAuthorization(
        authorization_id=str(raw["authorization_id"]),
        root_id=str(raw["root_id"]),
        sibling_root_id=str(raw["sibling_root_id"]),
        source_campaign_root_id=str(raw["source_campaign_root_id"]),
        symbol=str(raw["symbol"]),
        family=CommonEpisodeFamily(str(raw["family"])),
        side=_side(str(raw["side"])),
        attack_side=_side(str(raw["attack_side"])),
        attack_time_ns=int(raw["attack_time_ns"]),
        candidate_time_ns=int(raw["candidate_time_ns"]),
        participants=tuple(str(item) for item in raw["participants"]),
        participant_source_roots=tuple((str(pair[0]), str(pair[1])) for pair in raw["participant_source_roots"]),
        responsibility=str(raw["responsibility"]),
        attack_inventory_interpretation=InventoryInterpretation(str(raw["attack_inventory_interpretation"])),
        attack_inventory_decision_time_ns=int(raw["attack_inventory_decision_time_ns"]),
        attack_inventory_observed_time_ns=int(raw["attack_inventory_observed_time_ns"]),
        latest_inventory_interpretation=InventoryInterpretation(str(raw["latest_inventory_interpretation"])),
        latest_inventory_regime=InventoryRegime(str(raw["latest_inventory_regime"])),
        latest_inventory_decision_time_ns=int(raw["latest_inventory_decision_time_ns"]),
        latest_inventory_observed_time_ns=int(raw["latest_inventory_observed_time_ns"]),
        first_position_reset_observed_time_ns=(None if raw["first_position_reset_observed_time_ns"] is None else int(raw["first_position_reset_observed_time_ns"])),
        first_counter_sponsorship_observed_time_ns=(None if raw["first_counter_sponsorship_observed_time_ns"] is None else int(raw["first_counter_sponsorship_observed_time_ns"])),
    )


class CommonEpisodeLedger:
    """Coordinator-level common root and inventory-responsibility ledger."""

    STATE_VERSION = 1

    def __init__(self) -> None:
        self._records: dict[str, _CommonEpisodeRecord] = {}
        self._native_to_shared: dict[str, str] = {}
        self._authorizations: dict[str, CommonCandidateAuthorization] = {}
        self._invalidated_authorizations: dict[str, str] = {}

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(sorted(self._records))

    @property
    def invalidated_authorizations(self) -> Mapping[str, str]:
        return dict(self._invalidated_authorizations)

    def export_state(self) -> dict[str, object]:
        """Return a complete JSON-compatible checkpoint."""

        records: list[dict[str, object]] = []
        for root_id in sorted(self._records):
            record = self._records[root_id]
            attack = record.attack
            records.append({
                "attack": {
                    "root_id": attack.root_id,
                    "sibling_root_id": attack.sibling_root_id,
                    "attack_time_ns": attack.attack_time_ns,
                    "attack_side": attack.attack_side,
                    "participants": list(attack.participants),
                    "participant_source_roots": [list(pair) for pair in attack.participant_source_roots],
                    "attack_inventory": [
                        {"symbol": symbol, "decision": _inventory_to_payload(decision)}
                        for symbol, decision in attack.attack_inventory
                    ],
                },
                "frozen_attack_inventory": [
                    {
                        "symbol": symbol,
                        "decision": None if record.frozen_attack_inventory[symbol] is None else _inventory_to_payload(record.frozen_attack_inventory[symbol]),
                    }
                    for symbol in attack.participants
                ],
                "latest_inventory": [
                    {"symbol": symbol, "decision": _inventory_to_payload(record.latest_inventory[symbol])}
                    for symbol in attack.participants
                ],
                "participant_join_time_ns": [[symbol, record.participant_join_time_ns[symbol]] for symbol in attack.participants],
                "latest_official_observed_time_ns": [[symbol, record.latest_official_observed_time_ns[symbol]] for symbol in attack.participants],
                "first_position_reset_observed_time_ns": [[symbol, record.first_position_reset_observed_time_ns[symbol]] for symbol in attack.participants],
                "first_counter_sponsorship_observed_time_ns": [[symbol, record.first_counter_sponsorship_observed_time_ns[symbol]] for symbol in attack.participants],
                "state": record.state.value,
                "active_authorization_ids": sorted(record.active_authorization_ids),
                "claimed_authorization_id": record.claimed_authorization_id,
                "terminal_reason": record.terminal_reason,
            })
        return {
            "version": self.STATE_VERSION,
            "records": records,
            "native_to_shared": [list(pair) for pair in sorted(self._native_to_shared.items())],
            "authorizations": [
                _authorization_to_payload(self._authorizations[key])
                for key in sorted(self._authorizations)
            ],
            "invalidated_authorizations": [list(pair) for pair in sorted(self._invalidated_authorizations.items())],
        }

    @classmethod
    def restore_state(cls, payload: Mapping[str, Any]) -> "CommonEpisodeLedger":
        """Validate an entire checkpoint before publishing a restored object."""

        if int(payload.get("version", -1)) != cls.STATE_VERSION:
            raise CommonEpisodeError("unsupported common episode state version")
        try:
            collection_names = (
                "records", "native_to_shared", "authorizations", "invalidated_authorizations"
            )
            collections = {name: payload[name] for name in collection_names}
            if any(not isinstance(value, list) for value in collections.values()):
                raise CommonEpisodeError("common episode state collections must be lists")

            records: dict[str, _CommonEpisodeRecord] = {}
            derived_native: dict[str, str] = {}
            for raw_record in collections["records"]:
                if not isinstance(raw_record, Mapping) or not isinstance(raw_record["attack"], Mapping):
                    raise CommonEpisodeError("common episode record must be a mapping")
                raw_attack = raw_record["attack"]
                participants = tuple(str(item) for item in raw_attack["participants"])
                expected_order = tuple(symbol for symbol in SYMBOLS if symbol in participants)
                if len(participants) < 3 or len(set(participants)) != len(participants) or participants != expected_order:
                    raise CommonEpisodeError("invalid common attack participants")
                source_pairs = tuple((str(pair[0]), str(pair[1])) for pair in raw_attack["participant_source_roots"])
                if (
                    not source_pairs
                    or len(set(source_pairs)) != len(source_pairs)
                    or len({source for _, source in source_pairs}) != len(source_pairs)
                    or any(symbol not in participants or not source.strip() for symbol, source in source_pairs)
                    or any(not any(pair[0] == symbol for pair in source_pairs) for symbol in participants)
                ):
                    raise CommonEpisodeError("invalid participant source genealogy")
                attack_inventory = tuple(
                    (str(item["symbol"]), _inventory_from_payload(item["decision"]))
                    for item in raw_attack["attack_inventory"]
                )
                if tuple(symbol for symbol, _ in attack_inventory) != participants:
                    raise CommonEpisodeError("attack inventory participant mismatch")
                root_id = str(raw_attack["root_id"])
                attack = CommonAttack(
                    root_id=root_id,
                    sibling_root_id=str(raw_attack["sibling_root_id"]),
                    attack_time_ns=int(raw_attack["attack_time_ns"]),
                    attack_side=_side(str(raw_attack["attack_side"])),
                    participants=participants,
                    participant_source_roots=source_pairs,
                    attack_inventory=attack_inventory,
                )
                if not root_id.strip() or root_id in records or not attack.sibling_root_id.strip() or attack.attack_time_ns <= 0:
                    raise CommonEpisodeError("invalid or duplicate common root identity")

                def _decisions(name: str, optional: bool) -> dict[str, InventoryDecision | None]:
                    result: dict[str, InventoryDecision | None] = {}
                    for item in raw_record[name]:
                        symbol = str(item["symbol"])
                        decision_raw = item["decision"]
                        decision = None if optional and decision_raw is None else _inventory_from_payload(decision_raw)
                        if symbol in result:
                            raise CommonEpisodeError(f"duplicate {name} symbol")
                        result[symbol] = decision
                    if set(result) != set(participants):
                        raise CommonEpisodeError(f"{name} participant mismatch")
                    for symbol, decision in result.items():
                        if decision is not None and decision.symbol != symbol:
                            raise CommonEpisodeError(f"{name} symbol mismatch")
                    return result

                def _times(name: str) -> dict[str, int | None]:
                    result: dict[str, int | None] = {}
                    for pair in raw_record[name]:
                        symbol = str(pair[0])
                        if symbol in result:
                            raise CommonEpisodeError(f"duplicate {name} symbol")
                        result[symbol] = None if pair[1] is None else int(pair[1])
                    if set(result) != set(participants):
                        raise CommonEpisodeError(f"{name} participant mismatch")
                    return result

                frozen = _decisions("frozen_attack_inventory", True)
                latest_optional = _decisions("latest_inventory", False)
                if any(value is None for value in latest_optional.values()):
                    raise CommonEpisodeError("latest inventory cannot be null")
                latest = {symbol: value for symbol, value in latest_optional.items() if value is not None}
                join_optional = _times("participant_join_time_ns")
                if any(value is None for value in join_optional.values()):
                    raise CommonEpisodeError("participant join time cannot be null")
                joins = {symbol: value for symbol, value in join_optional.items() if value is not None}
                latest_observed = _times("latest_official_observed_time_ns")
                first_reset = _times("first_position_reset_observed_time_ns")
                first_counter = _times("first_counter_sponsorship_observed_time_ns")
                registration_by_symbol = dict(attack_inventory)
                for symbol in participants:
                    registration = registration_by_symbol[symbol]
                    current = latest[symbol]
                    if joins[symbol] < attack.attack_time_ns:
                        raise CommonEpisodeError("participant joined before common attack")
                    if registration.decision_ts_ns != joins[symbol]:
                        raise CommonEpisodeError("registration inventory does not match join clock")
                    if registration.symbol != symbol or _inventory_side(registration.shock_side) != attack.attack_side:
                        raise CommonEpisodeError("registration inventory mismatch")
                    if current.symbol != symbol or _inventory_side(current.shock_side) != attack.attack_side:
                        raise CommonEpisodeError("latest inventory mismatch")
                    if current.current_observed_ts_ns != latest_observed[symbol] and (current.known or latest_observed[symbol] is not None):
                        raise CommonEpisodeError("latest official clock mismatch")
                    frozen_value = frozen[symbol]
                    if frozen_value is not None and (not frozen_value.known or frozen_value.current_observed_ts_ns is None):
                        raise CommonEpisodeError("effective frozen inventory is invalid")
                    for milestone in (first_reset[symbol], first_counter[symbol]):
                        if milestone is not None and milestone <= joins[symbol]:
                            raise CommonEpisodeError("inventory milestone precedes join")

                state = CommonEpisodeState(str(raw_record["state"]))
                active = {str(item) for item in raw_record["active_authorization_ids"]}
                claimed = None if raw_record["claimed_authorization_id"] is None else str(raw_record["claimed_authorization_id"])
                reason = None if raw_record["terminal_reason"] is None else str(raw_record["terminal_reason"])
                if state is CommonEpisodeState.OPEN and (claimed is not None or reason is not None):
                    raise CommonEpisodeError("open common root has terminal fields")
                if state is CommonEpisodeState.CLAIMED and (claimed is None or active != {claimed} or not reason):
                    raise CommonEpisodeError("claimed common root lacks claim state")
                if state is CommonEpisodeState.INVALIDATED and (active or claimed is not None or not reason):
                    raise CommonEpisodeError("invalidated common root has inconsistent state")
                records[root_id] = _CommonEpisodeRecord(
                    attack=attack,
                    frozen_attack_inventory=frozen,
                    latest_inventory=latest,
                    participant_join_time_ns=joins,
                    latest_official_observed_time_ns=latest_observed,
                    first_position_reset_observed_time_ns=first_reset,
                    first_counter_sponsorship_observed_time_ns=first_counter,
                    state=state,
                    active_authorization_ids=active,
                    claimed_authorization_id=claimed,
                    terminal_reason=reason,
                )
                for _, native in source_pairs:
                    prior = derived_native.setdefault(native, root_id)
                    if prior != root_id:
                        raise CommonEpisodeError("native source belongs to two common roots")

            native_to_shared = {str(pair[0]): str(pair[1]) for pair in collections["native_to_shared"]}
            if len(native_to_shared) != len(collections["native_to_shared"]) or native_to_shared != derived_native:
                raise CommonEpisodeError("native-to-shared mapping is inconsistent")
            authorizations: dict[str, CommonCandidateAuthorization] = {}
            for raw in collections["authorizations"]:
                if not isinstance(raw, Mapping):
                    raise CommonEpisodeError("authorization must be a mapping")
                authorization = _authorization_from_payload(raw)
                record = records.get(authorization.root_id)
                if not authorization.authorization_id.strip() or authorization.authorization_id in authorizations or record is None:
                    raise CommonEpisodeError("invalid or duplicate authorization")
                attack = record.attack
                if (
                    authorization.sibling_root_id != attack.sibling_root_id
                    or authorization.attack_side != attack.attack_side
                    or authorization.attack_time_ns != attack.attack_time_ns
                    or authorization.participants != attack.participants
                    or authorization.participant_source_roots != attack.participant_source_roots
                    or authorization.symbol not in attack.participants
                    or authorization.source_campaign_root_id not in attack.source_roots_for(authorization.symbol)
                ):
                    raise CommonEpisodeError("authorization genealogy mismatch")
                authorizations[authorization.authorization_id] = authorization
            for record in records.values():
                if any(item not in authorizations or authorizations[item].root_id != record.attack.root_id for item in record.active_authorization_ids):
                    raise CommonEpisodeError("active authorization mapping is invalid")
            invalidated: dict[str, str] = {}
            for pair in collections["invalidated_authorizations"]:
                authorization_id, reason = str(pair[0]), str(pair[1])
                if not reason or authorization_id in invalidated or authorization_id not in authorizations:
                    raise CommonEpisodeError("invalid invalidated-authorization state")
                invalidated[authorization_id] = reason
            if any(
                authorization_id in record.active_authorization_ids
                for authorization_id in invalidated
                for record in records.values()
            ):
                raise CommonEpisodeError("invalidated authorization is still active")
        except CommonEpisodeError:
            raise
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise CommonEpisodeError("malformed common episode state") from exc

        ledger = cls()
        ledger._records = records
        ledger._native_to_shared = native_to_shared
        ledger._authorizations = authorizations
        ledger._invalidated_authorizations = invalidated
        return ledger

    def register_attack(
        self,
        *,
        attack_time_ns: int,
        attack_side: str,
        source_campaign_roots: Mapping[str, str | Sequence[str]],
        attack_inventory: Mapping[str, InventoryDecision],
    ) -> CommonAttack:
        """Register one physical >=3-market attack under one shared root.

        The participant set is the set of supplied native campaign roots.  A
        root is therefore a function only of attack time, side, participants,
        and their source identities; inventory interpretation cannot change
        the physical identity.
        """

        side = _side(attack_side)
        if attack_time_ns <= 0:
            raise CommonEpisodeError("attack_time_ns must be positive")
        unsupported = set(source_campaign_roots) - set(SYMBOLS)
        if unsupported:
            raise CommonEpisodeError(f"unsupported participants: {sorted(unsupported)}")
        participants = tuple(
            symbol for symbol in SYMBOLS if symbol in source_campaign_roots
        )
        if len(participants) < 3:
            raise CommonEpisodeError("a common outside attack requires >=3 markets")
        if set(attack_inventory) != set(participants):
            raise CommonEpisodeError(
                "frozen attack inventory must exist for every participant only"
            )

        roots: list[tuple[str, str]] = []
        frozen_inventory: list[tuple[str, InventoryDecision]] = []
        for symbol in participants:
            supplied_roots = source_campaign_roots[symbol]
            if isinstance(supplied_roots, str):
                normalized_roots = (supplied_roots,)
            else:
                normalized_roots = tuple(supplied_roots)
            if not normalized_roots or any(
                not isinstance(source_root, str) or not source_root.strip()
                for source_root in normalized_roots
            ):
                raise CommonEpisodeError("source campaign roots must be non-empty")
            normalized_roots = tuple(sorted(set(normalized_roots)))
            decision = attack_inventory[symbol]
            if decision.symbol != symbol:
                raise CommonEpisodeError("attack inventory belongs to another symbol")
            if decision.decision_ts_ns > attack_time_ns:
                raise CommonEpisodeError("future inventory cannot be frozen at attack")
            if (
                decision.current_observed_ts_ns is not None
                and decision.current_observed_ts_ns > attack_time_ns
            ):
                raise CommonEpisodeError(
                    "future official inventory cannot be frozen at attack"
                )
            if decision.known and decision.current_observed_ts_ns is None:
                raise CommonEpisodeError(
                    "known attack inventory requires an official observation time"
                )
            if _inventory_side(decision.shock_side) != side:
                raise CommonEpisodeError("attack inventory shock side differs from attack")
            roots.extend((symbol, source_root) for source_root in normalized_roots)
            frozen_inventory.append((symbol, decision))

        root_id = stable_id(
            "COMMON_STRUCTURAL_OUTSIDE_ATTACK",
            attack_time_ns,
            side,
            *(f"{symbol}:{source_root}" for symbol, source_root in roots),
            prefix="common-episode-",
        )
        sibling_root_id = stable_id(
            root_id,
            "SHARED_PARTICIPANT_SIBLINGS",
            prefix="common-siblings-",
        )
        attack = CommonAttack(
            root_id=root_id,
            sibling_root_id=sibling_root_id,
            attack_time_ns=attack_time_ns,
            attack_side=side,
            participants=participants,
            participant_source_roots=tuple(roots),
            attack_inventory=tuple(frozen_inventory),
        )
        existing = self._records.get(root_id)
        if existing is not None:
            if existing.attack != attack:
                raise CommonEpisodeError("common root identity collision")
            return existing.attack

        for _, source_root in roots:
            prior_shared = self._native_to_shared.get(source_root)
            if prior_shared is not None and prior_shared != root_id:
                raise CommonEpisodeError(
                    f"native campaign {source_root} already belongs to {prior_shared}"
                )
        self._records[root_id] = _CommonEpisodeRecord(
            attack=attack,
            frozen_attack_inventory={
                symbol: decision if decision.known else None
                for symbol, decision in frozen_inventory
            },
            latest_inventory=dict(frozen_inventory),
            participant_join_time_ns={
                symbol: attack_time_ns for symbol in participants
            },
            latest_official_observed_time_ns={
                symbol: (
                    decision.current_observed_ts_ns if decision.known else None
                )
                for symbol, decision in frozen_inventory
            },
            first_position_reset_observed_time_ns={
                symbol: None for symbol in participants
            },
            first_counter_sponsorship_observed_time_ns={
                symbol: None for symbol in participants
            },
        )
        for _, source_root in roots:
            self._native_to_shared[source_root] = root_id
        return attack

    def extend_attack(
        self,
        root_or_native: str,
        source_campaign_roots: Mapping[str, str | Sequence[str]],
        join_inventory: Mapping[str, InventoryDecision],
    ) -> CommonAttack:
        """Attach later same-wave sources or participants to a shared root.

        The root and sibling identity stay fixed at the first broad attack.
        Existing symbols only gain aliases; their frozen mechanism is never
        replaced.  A new symbol freezes known inventory at its join decision,
        or waits for its first strictly-later known official observation.
        """

        record = self._record(root_or_native)
        if record.state is not CommonEpisodeState.OPEN:
            raise CommonEpisodeError("cannot extend a terminal common episode")
        if not source_campaign_roots:
            return record.attack
        unsupported = set(source_campaign_roots) - set(SYMBOLS)
        if unsupported:
            raise CommonEpisodeError(f"unsupported participants: {sorted(unsupported)}")
        if set(join_inventory) != set(source_campaign_roots):
            raise CommonEpisodeError(
                "join inventory must exist for every extended participant only"
            )

        additions: list[tuple[str, str]] = []
        for symbol in SYMBOLS:
            if symbol not in source_campaign_roots:
                continue
            supplied = source_campaign_roots[symbol]
            roots = (supplied,) if isinstance(supplied, str) else tuple(supplied)
            if not roots or any(
                not isinstance(source_root, str) or not source_root.strip()
                for source_root in roots
            ):
                raise CommonEpisodeError("source campaign roots must be non-empty")
            roots = tuple(sorted(set(roots)))
            additions.extend((symbol, source_root) for source_root in roots)

            decision = join_inventory[symbol]
            if decision.symbol != symbol:
                raise CommonEpisodeError("join inventory belongs to another symbol")
            if decision.decision_ts_ns < record.attack.attack_time_ns:
                raise CommonEpisodeError("a participant cannot join before the attack")
            if (
                decision.current_observed_ts_ns is not None
                and decision.current_observed_ts_ns > decision.decision_ts_ns
            ):
                raise CommonEpisodeError("future official inventory cannot be used at join")
            if decision.known and decision.current_observed_ts_ns is None:
                raise CommonEpisodeError(
                    "known join inventory requires an official observation time"
                )
            if _inventory_side(decision.shock_side) != record.attack.attack_side:
                raise CommonEpisodeError("join inventory shock side differs from attack")

        for _, source_root in additions:
            prior_shared = self._native_to_shared.get(source_root)
            if prior_shared is not None and prior_shared != record.attack.root_id:
                raise CommonEpisodeError(
                    f"native campaign {source_root} already belongs to {prior_shared}"
                )

        old_attack = record.attack
        prior_participants = set(old_attack.participants)
        root_pairs = set(old_attack.participant_source_roots)
        root_pairs.update(additions)
        ordered_pairs = tuple(
            pair
            for symbol in SYMBOLS
            for pair in sorted(
                (item for item in root_pairs if item[0] == symbol),
                key=lambda item: item[1],
            )
        )
        joined_symbols = set(source_campaign_roots)
        participants = tuple(
            symbol
            for symbol in SYMBOLS
            if symbol in prior_participants or symbol in joined_symbols
        )
        registration_inventory = dict(old_attack.attack_inventory)

        for symbol in participants:
            if symbol in prior_participants:
                continue
            decision = join_inventory[symbol]
            registration_inventory[symbol] = decision
            record.participant_join_time_ns[symbol] = decision.decision_ts_ns
            record.frozen_attack_inventory[symbol] = (
                decision if decision.known else None
            )
            record.latest_inventory[symbol] = decision
            record.latest_official_observed_time_ns[symbol] = (
                decision.current_observed_ts_ns if decision.known else None
            )
            record.first_position_reset_observed_time_ns[symbol] = None
            record.first_counter_sponsorship_observed_time_ns[symbol] = None

        record.attack = CommonAttack(
            root_id=old_attack.root_id,
            sibling_root_id=old_attack.sibling_root_id,
            attack_time_ns=old_attack.attack_time_ns,
            attack_side=old_attack.attack_side,
            participants=participants,
            participant_source_roots=ordered_pairs,
            attack_inventory=tuple(
                (symbol, registration_inventory[symbol]) for symbol in participants
            ),
        )
        for _, source_root in additions:
            self._native_to_shared[source_root] = record.attack.root_id

        # Existing participants gain source aliases only.  Their temporal
        # inventory must continue to be evaluated against the frozen first
        # attack bar by the coordinator's regular update path; re-evaluating
        # with this later alias bar would change the mechanism mid-episode.
        return record.attack

    def shared_root_for(self, root_or_native: str) -> str:
        if root_or_native in self._records:
            return root_or_native
        try:
            return self._native_to_shared[root_or_native]
        except KeyError as exc:
            raise CommonEpisodeError(
                f"unknown common or native campaign root: {root_or_native}"
            ) from exc

    def attack(self, root_or_native: str) -> CommonAttack:
        return self._record(root_or_native).attack

    def state(self, root_or_native: str) -> CommonEpisodeState:
        return self._record(root_or_native).state

    def frozen_inventory_for(
        self,
        root_or_native: str,
        symbol: str,
    ) -> InventoryDecision | None:
        """Return the effective attack observation, if it is known yet."""

        record = self._record(root_or_native)
        if symbol not in record.attack.participants:
            raise CommonEpisodeError("symbol did not participate in the common attack")
        return record.frozen_attack_inventory[symbol]

    def update_inventory(
        self,
        root_or_native: str,
        decisions: Mapping[str, InventoryDecision],
    ) -> None:
        """Advance inventory only when a new official metric is observable.

        ``decision_ts_ns`` advances every replay minute even while the same
        five-minute official row remains the latest visible observation.  It
        therefore cannot establish temporal responsibility.  The causal
        clock here is ``current_observed_ts_ns``: repeated classifications of
        the same row are no-ops, and unknown rows never become evidence.
        """

        record = self._record(root_or_native)
        if record.state is not CommonEpisodeState.OPEN:
            raise CommonEpisodeError("cannot update a terminal common episode")
        for symbol, decision in decisions.items():
            if symbol not in record.attack.participants or decision.symbol != symbol:
                raise CommonEpisodeError("inventory update is not a participant decision")
            if decision.decision_ts_ns <= record.attack.attack_time_ns:
                raise CommonEpisodeError("latest inventory must be strictly after attack")
            if _inventory_side(decision.shock_side) != record.attack.attack_side:
                raise CommonEpisodeError("inventory update shock side differs from attack")
        for symbol, decision in decisions.items():
            self._advance_inventory(record, symbol, decision)

    def authorize_candidate(
        self,
        root_or_native: str,
        *,
        symbol: str,
        family: CommonEpisodeFamily | str,
        side: str,
        candidate_time_ns: int,
        source_campaign_root_id: str | None = None,
    ) -> CommonCandidateAuthorization | None:
        """Authorize CIRB responsibility after a native candidate completes.

        Returning ``None`` means the inventory mechanism does not own that
        branch.  It does not invalidate, rewind, or otherwise alter the native
        price campaign.
        """

        record = self._record(root_or_native)
        if record.state is not CommonEpisodeState.OPEN:
            return None
        attack = record.attack
        if symbol not in attack.participants:
            raise CommonEpisodeError("candidate symbol did not join the common attack")
        try:
            branch = family if isinstance(family, CommonEpisodeFamily) else CommonEpisodeFamily(family)
        except ValueError as exc:
            raise CommonEpisodeError(f"unsupported common family: {family}") from exc
        candidate_side = _side(side)
        intended_side = (
            attack.attack_side
            if branch is CommonEpisodeFamily.CONTINUATION
            else _opposite(attack.attack_side)
        )
        if candidate_side != intended_side:
            raise CommonEpisodeError("candidate side contradicts its common branch")
        if candidate_time_ns <= attack.attack_time_ns:
            raise CommonEpisodeError("a completed common candidate must follow its attack")

        if source_campaign_root_id is None:
            symbol_roots = attack.source_roots_for(symbol)
            if root_or_native in symbol_roots:
                source_campaign_root_id = root_or_native
        exact_source_root = attack.source_root_for(
            symbol,
            source_campaign_root_id,
        )
        frozen = record.frozen_attack_inventory[symbol]
        if frozen is None:
            return None
        latest = record.latest_inventory[symbol]
        frozen_observed = frozen.current_observed_ts_ns
        latest_observed = latest.current_observed_ts_ns
        if frozen_observed is None or latest_observed is None:
            raise CommonEpisodeError("known inventory is missing its official clock")
        if latest_observed > candidate_time_ns:
            raise CommonEpisodeError("authorization would use future inventory")
        if latest.decision_ts_ns > candidate_time_ns:
            raise CommonEpisodeError(
                "authorization inventory decision is later than the candidate",
            )
        position_reset_observed = (
            record.first_position_reset_observed_time_ns[symbol]
        )
        counter_sponsorship_observed = (
            record.first_counter_sponsorship_observed_time_ns[symbol]
        )
        responsibility = self._responsibility(
            branch=branch,
            frozen=frozen,
            first_position_reset_observed_time_ns=position_reset_observed,
            first_counter_sponsorship_observed_time_ns=(
                counter_sponsorship_observed
            ),
        )
        if responsibility is None:
            return None

        authorization_id = stable_id(
            attack.root_id,
            branch.value,
            symbol,
            exact_source_root,
            candidate_time_ns,
            prefix="common-authorization-",
        )
        authorization = CommonCandidateAuthorization(
            authorization_id=authorization_id,
            root_id=attack.root_id,
            sibling_root_id=attack.sibling_root_id,
            source_campaign_root_id=exact_source_root,
            symbol=symbol,
            family=branch,
            side=candidate_side,
            attack_side=attack.attack_side,
            attack_time_ns=attack.attack_time_ns,
            candidate_time_ns=candidate_time_ns,
            participants=attack.participants,
            participant_source_roots=attack.participant_source_roots,
            responsibility=responsibility,
            attack_inventory_interpretation=frozen.interpretation,
            attack_inventory_decision_time_ns=frozen.decision_ts_ns,
            attack_inventory_observed_time_ns=frozen_observed,
            latest_inventory_interpretation=latest.interpretation,
            latest_inventory_regime=latest.regime,
            latest_inventory_decision_time_ns=latest.decision_ts_ns,
            latest_inventory_observed_time_ns=latest_observed,
            first_position_reset_observed_time_ns=position_reset_observed,
            first_counter_sponsorship_observed_time_ns=(
                counter_sponsorship_observed
            ),
        )
        existing = self._authorizations.get(authorization_id)
        if existing is not None and existing != authorization:
            raise CommonEpisodeError("common authorization identity collision")
        self._authorizations[authorization_id] = authorization
        record.active_authorization_ids.add(authorization_id)
        return authorization

    def validate_claim(self, authorization_id: str) -> None:
        """Validate a coordinator claim without mutating shared state."""

        try:
            authorization = self._authorizations[authorization_id]
        except KeyError as exc:
            raise CommonEpisodeError("unknown common authorization") from exc
        record = self._records[authorization.root_id]
        if record.state is not CommonEpisodeState.OPEN:
            raise CommonEpisodeError("common episode is already terminal")

    def claim(self, authorization_id: str) -> tuple[str, ...]:
        """Claim one candidate and consume every authorization under its root."""

        self.validate_claim(authorization_id)
        authorization = self._authorizations[authorization_id]
        record = self._records[authorization.root_id]
        siblings = tuple(sorted(record.active_authorization_ids - {authorization_id}))
        for sibling in siblings:
            self._invalidated_authorizations[sibling] = "SIBLING_CLAIMED"
        record.active_authorization_ids = {authorization_id}
        record.claimed_authorization_id = authorization_id
        record.state = CommonEpisodeState.CLAIMED
        record.terminal_reason = "ACCOUNT_CLAIM"
        return siblings

    def invalidate(self, root_or_native: str, *, reason: str) -> tuple[str, ...]:
        """Terminally invalidate an unclaimed root for a physical reason."""

        if not reason:
            raise CommonEpisodeError("physical invalidation reason must be non-empty")
        record = self._record(root_or_native)
        if record.state is CommonEpisodeState.CLAIMED:
            raise CommonEpisodeError("a claimed root belongs to execution lifecycle")
        if record.state is CommonEpisodeState.INVALIDATED:
            return ()
        invalidated = tuple(sorted(record.active_authorization_ids))
        for authorization_id in invalidated:
            self._invalidated_authorizations[authorization_id] = reason
        record.active_authorization_ids.clear()
        record.state = CommonEpisodeState.INVALIDATED
        record.terminal_reason = reason
        return invalidated

    @staticmethod
    def _advance_inventory(
        record: _CommonEpisodeRecord,
        symbol: str,
        decision: InventoryDecision,
    ) -> None:
        """Preserve first official causal milestones while advancing latest."""

        observed = decision.current_observed_ts_ns
        if not decision.known or observed is None:
            return
        prior_observed = record.latest_official_observed_time_ns[symbol]
        if prior_observed is not None and observed <= prior_observed:
            return

        frozen = record.frozen_attack_inventory[symbol]
        if frozen is None:
            # The attack/join placeholder was unknown.  Only an official row
            # first observable strictly after that physical join can freeze
            # the participant's inventory mechanism.
            if observed <= record.participant_join_time_ns[symbol]:
                return
            record.frozen_attack_inventory[symbol] = decision
            record.latest_inventory[symbol] = decision
            record.latest_official_observed_time_ns[symbol] = observed
            return

        frozen_observed = frozen.current_observed_ts_ns
        if frozen_observed is None:
            raise CommonEpisodeError("frozen inventory has no official observation")
        if observed <= frozen_observed:
            return
        if (
            decision.regime is InventoryRegime.POSITION_RESET
            and observed > record.participant_join_time_ns[symbol]
            and record.first_position_reset_observed_time_ns[symbol] is None
        ):
            record.first_position_reset_observed_time_ns[symbol] = observed
        if (
            decision.interpretation
            is InventoryInterpretation.FRESH_SPONSORSHIP_COUNTER_INVENTORY
            and observed > record.participant_join_time_ns[symbol]
            and record.first_counter_sponsorship_observed_time_ns[symbol] is None
        ):
            record.first_counter_sponsorship_observed_time_ns[symbol] = observed
        record.latest_inventory[symbol] = decision
        record.latest_official_observed_time_ns[symbol] = observed

    @staticmethod
    def _responsibility(
        *,
        branch: CommonEpisodeFamily,
        frozen: InventoryDecision,
        first_position_reset_observed_time_ns: int | None,
        first_counter_sponsorship_observed_time_ns: int | None,
    ) -> str | None:
        attack = frozen.interpretation
        if branch is CommonEpisodeFamily.REVERSAL:
            return (
                "FROZEN_FORCED_DISCHARGE_FAILED"
                if attack
                is InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE
                else None
            )

        if attack in {
            InventoryInterpretation.FRESH_SPONSORSHIP_CROWDING,
            InventoryInterpretation.FRESH_SPONSORSHIP_COUNTER_INVENTORY,
        }:
            return "ATTACK_FRESH_SPONSORSHIP"
        if (
            attack is InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE
            and first_position_reset_observed_time_ns is not None
        ):
            return "LATER_POSITION_RESET_RETAINS_CONTINUATION"
        if (
            attack
            is InventoryInterpretation.FORCED_DELEVERAGING_COUNTER_INVENTORY
            and first_counter_sponsorship_observed_time_ns is not None
        ):
            return "LATER_COUNTER_INVENTORY_SPONSORSHIP"
        return None

    def _record(self, root_or_native: str) -> _CommonEpisodeRecord:
        root_id = self.shared_root_for(root_or_native)
        return self._records[root_id]


__all__ = [
    "CommonAttack",
    "CommonCandidateAuthorization",
    "CommonEpisodeError",
    "CommonEpisodeFamily",
    "CommonEpisodeLedger",
    "CommonEpisodeState",
]
