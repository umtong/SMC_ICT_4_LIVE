"""Online generative inference for source-bound market ownership.

The filter deliberately does not predict trade outcomes.  It compares distinct
source owner identities with a ``NONE`` hypothesis and, inside each identity,
mixes four explicit-duration linear-Gaussian phase models.  Source identities
remain categorical hypotheses for their entire lifetime; their continuous
state moments are never pooled.

All observations are expected to be aligned to the identity direction.  A
positive residual return or progress therefore means progress by that identity,
irrespective of whether its market direction is long or short.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
from typing import Any, Mapping


class LatentOwnerError(ValueError):
    """The source-bound filtering contract was violated."""


class DirectOwnerFlipError(LatentOwnerError):
    """An opposite identity was introduced without a NONE/supersession step."""


class OwnerDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OwnerPhase(str, Enum):
    CONTEST = "CONTEST"
    RELEASE = "RELEASE"
    DEFENDED_RETURN = "DEFENDED_RETURN"
    DELIVERY = "DELIVERY"


class TerminalReason(str, Enum):
    TARGET_CONSUMED = "TARGET_CONSUMED"
    STRUCTURAL_INVALIDATION = "STRUCTURAL_INVALIDATION"
    EXPLICIT_SUPERSESSION = "EXPLICIT_SUPERSESSION"


@dataclass(frozen=True, order=True, slots=True)
class OwnerIdentity:
    """Exact structural owner identity; generation prevents source-id reuse."""

    source_id: str
    generation: int
    direction: OwnerDirection

    def __post_init__(self) -> None:
        if not self.source_id:
            raise LatentOwnerError("source_id cannot be empty")
        if self.generation < 1:
            raise LatentOwnerError("generation must be positive")

    @property
    def token(self) -> str:
        return f"{self.source_id}\x1f{self.generation}\x1f{self.direction.value}"

    @classmethod
    def from_token(cls, token: str) -> "OwnerIdentity":
        try:
            source_id, generation, direction = token.split("\x1f")
            return cls(source_id, int(generation), OwnerDirection(direction))
        except (TypeError, ValueError) as exc:
            raise LatentOwnerError("invalid owner identity token") from exc


_OBSERVATION_FIELDS = (
    "return_progress",
    "source_progress",
    "spot_flow",
    "perp_flow",
    "impact_per_flow",
    "distance_from_source",
    "target_progress",
    "common_nuisance",
    "residual_return",
    "open_interest_change",
    "basis_change",
    "depth_imbalance",
)

# A single close is represented once in the likelihood.  Other affine views
# remain available for diagnostics, but multiplying their scalar Gaussian
# likelihoods would manufacture certainty from duplicated evidence.
_INFERENCE_FIELDS = (
    "source_progress",
    "spot_flow",
    "perp_flow",
    "residual_return",
    "open_interest_change",
    "basis_change",
    "depth_imbalance",
)


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """Causal, source-aligned observation; ``None`` dimensions are marginalized."""

    time_ns: int
    return_progress: float | None = None
    source_progress: float | None = None
    spot_flow: float | None = None
    perp_flow: float | None = None
    impact_per_flow: float | None = None
    distance_from_source: float | None = None
    target_progress: float | None = None
    common_nuisance: float | None = None
    residual_return: float | None = None
    open_interest_change: float | None = None
    basis_change: float | None = None
    depth_imbalance: float | None = None

    def __post_init__(self) -> None:
        if self.time_ns < 0:
            raise LatentOwnerError("observation time cannot be negative")
        for name in _OBSERVATION_FIELDS:
            value = getattr(self, name)
            if value is not None and not math.isfinite(float(value)):
                raise LatentOwnerError(f"{name} must be finite when observed")

    def available(self) -> tuple[tuple[str, float], ...]:
        return tuple(
            (name, float(value))
            for name in _INFERENCE_FIELDS
            if (value := getattr(self, name)) is not None
        )


@dataclass(frozen=True, slots=True)
class PhasePrior:
    """Broad parameters of one generative phase, not an entry score."""

    bias: Mapping[str, float]
    loading: Mapping[str, float]
    noise_variance: Mapping[str, float]
    process_variance: float
    duration_scale: float
    duration_shape: float


def _phase_prior(
    bias: tuple[float, ...],
    loading: tuple[float, ...],
    noise: tuple[float, ...],
    *,
    process_variance: float,
    duration_scale: float,
    duration_shape: float,
) -> PhasePrior:
    return PhasePrior(
        bias=dict(zip(_OBSERVATION_FIELDS, bias, strict=True)),
        loading=dict(zip(_OBSERVATION_FIELDS, loading, strict=True)),
        noise_variance=dict(zip(_OBSERVATION_FIELDS, noise, strict=True)),
        process_variance=process_variance,
        duration_scale=duration_scale,
        duration_shape=duration_shape,
    )


def _default_phase_priors() -> dict[OwnerPhase, PhasePrior]:
    # Values describe intentionally broad qualitative emission families.  They
    # are not fitted outcome labels and are not used as trading thresholds.
    noise = (
        0.90,
        0.90,
        1.30,
        1.30,
        1.20,
        1.10,
        0.95,
        2.20,
        0.90,
        1.80,
        1.40,
        1.50,
    )
    return {
        OwnerPhase.CONTEST: _phase_prior(
            (0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00, 0.00),
            (0.20, 0.15, 0.30, 0.30, 0.15, 0.10, 0.05, 0.02, 0.15, 0.08, 0.10, 0.15),
            noise,
            process_variance=0.20,
            duration_scale=4.0,
            duration_shape=1.25,
        ),
        OwnerPhase.RELEASE: _phase_prior(
            (0.12, 0.12, 0.10, 0.10, 0.08, 0.08, 0.05, 0.00, 0.12, 0.02, 0.05, 0.06),
            (0.70, 0.60, 0.55, 0.55, 0.50, 0.45, 0.30, 0.02, 0.70, 0.10, 0.30, 0.35),
            noise,
            process_variance=0.16,
            duration_scale=3.0,
            duration_shape=1.15,
        ),
        OwnerPhase.DEFENDED_RETURN: _phase_prior(
            (-0.10, 0.02, 0.02, 0.02, -0.02, -0.08, 0.02, 0.00, -0.08, 0.00, 0.01, 0.04),
            (0.35, 0.30, 0.40, 0.40, 0.25, 0.30, 0.15, 0.02, 0.35, 0.08, 0.22, 0.35),
            noise,
            process_variance=0.18,
            duration_scale=2.5,
            duration_shape=1.35,
        ),
        OwnerPhase.DELIVERY: _phase_prior(
            (0.20, 0.22, 0.12, 0.12, 0.10, 0.16, 0.18, 0.00, 0.20, 0.02, 0.08, 0.08),
            (0.80, 0.75, 0.45, 0.45, 0.50, 0.55, 0.65, 0.02, 0.80, 0.10, 0.40, 0.38),
            noise,
            process_variance=0.12,
            duration_scale=6.0,
            duration_shape=1.10,
        ),
    }


@dataclass(frozen=True, slots=True)
class LatentOwnerConfig:
    initial_phase_probability: Mapping[OwnerPhase, float] = field(
        default_factory=lambda: {
            OwnerPhase.CONTEST: 0.82,
            OwnerPhase.RELEASE: 0.12,
            OwnerPhase.DEFENDED_RETURN: 0.04,
            OwnerPhase.DELIVERY: 0.02,
        }
    )
    phase_priors: Mapping[OwnerPhase, PhasePrior] = field(default_factory=_default_phase_priors)
    new_identity_mass: float = 0.16
    initial_latent_mean: float = 0.0
    initial_latent_variance: float = 1.50
    none_noise_variance: float = 2.50
    minimum_variance: float = 1e-9
    minimum_probability: float = 1e-300

    def __post_init__(self) -> None:
        if not 0.0 < self.new_identity_mass < 1.0:
            raise LatentOwnerError("new_identity_mass must be between zero and one")
        if self.initial_latent_variance <= 0.0 or self.none_noise_variance <= 0.0:
            raise LatentOwnerError("variances must be positive")
        if set(self.initial_phase_probability) != set(OwnerPhase):
            raise LatentOwnerError("every owner phase needs an initial probability")
        if set(self.phase_priors) != set(OwnerPhase):
            raise LatentOwnerError("every owner phase needs a generative prior")
        total = sum(float(v) for v in self.initial_phase_probability.values())
        if total <= 0.0 or any(float(v) < 0.0 for v in self.initial_phase_probability.values()):
            raise LatentOwnerError("initial phase probabilities must be nonnegative")


@dataclass(slots=True)
class _ModeState:
    mean: float
    variance: float
    expected_age: float


@dataclass(slots=True)
class _IdentityState:
    key: OwnerIdentity
    probability: float
    phase_probability: dict[OwnerPhase, float]
    mode: dict[OwnerPhase, _ModeState]
    attack_count: int
    last_attack_time_ns: int
    last_observation_time_ns: int | None = None


@dataclass(frozen=True, slots=True)
class TerminalRecord:
    identity: OwnerIdentity
    reason: TerminalReason
    structure_id: str
    time_ns: int
    replacement: OwnerIdentity | None = None


@dataclass(frozen=True, slots=True)
class PosteriorView:
    none_probability: float
    identity_probability: Mapping[OwnerIdentity, float]
    phase_probability: Mapping[OwnerIdentity, Mapping[OwnerPhase, float]]
    joint_phase_probability: Mapping[OwnerIdentity, Mapping[OwnerPhase, float]]
    entropy: float


_DESTINATION_WEIGHTS: dict[OwnerPhase, dict[OwnerPhase, float]] = {
    OwnerPhase.CONTEST: {OwnerPhase.RELEASE: 1.0},
    OwnerPhase.RELEASE: {OwnerPhase.DEFENDED_RETURN: 0.58, OwnerPhase.DELIVERY: 0.42},
    OwnerPhase.DEFENDED_RETURN: {OwnerPhase.RELEASE: 0.36, OwnerPhase.DELIVERY: 0.64},
    OwnerPhase.DELIVERY: {OwnerPhase.DEFENDED_RETURN: 1.0},
}


def _normal_log_density(value: float, mean: float, variance: float) -> float:
    variance = max(variance, 1e-12)
    error = value - mean
    return -0.5 * (math.log(2.0 * math.pi * variance) + error * error / variance)


def _logsumexp(values: list[float]) -> float:
    if not values:
        return -math.inf
    maximum = max(values)
    if maximum == -math.inf:
        return maximum
    return maximum + math.log(sum(math.exp(value - maximum) for value in values))


class LatentOwnerFilter:
    """Pure online MMAE/IMM filter over exact source owner identities."""

    SNAPSHOT_VERSION = 1

    def __init__(self, config: LatentOwnerConfig | None = None) -> None:
        self.config = config or LatentOwnerConfig()
        self._none_probability = 1.0
        self._states: dict[OwnerIdentity, _IdentityState] = {}
        self._terminal: list[TerminalRecord] = []
        self._last_event_time_ns: int | None = None
        self._last_update_time_ns: int | None = None

    def register_attack(
        self,
        identity: OwnerIdentity,
        time_ns: int,
        observation: SourceObservation | None = None,
    ) -> OwnerIdentity:
        """Register an attack, reusing the exact identity on repeated attacks."""

        self._validate_time(time_ns)
        if observation is not None and observation.time_ns != time_ns:
            raise LatentOwnerError("attack observation and attack time must match")
        state = self._states.get(identity)
        if state is None:
            for other in self._states:
                if (
                    other.source_id == identity.source_id
                    and other.generation == identity.generation
                    and other.direction is not identity.direction
                ):
                    raise DirectOwnerFlipError(
                        "opposite owner identity requires structural termination or explicit supersession"
                    )
            self._add_identity(identity, time_ns)
            state = self._states[identity]
        else:
            state.attack_count += 1
            state.last_attack_time_ns = time_ns
            self._reset_attack_phase(state)
        self._last_event_time_ns = time_ns
        if observation is not None:
            self.update(identity, observation)
        return identity

    def register_competing_attack(
        self,
        source_id: str,
        generation: int,
        time_ns: int,
        directions: tuple[OwnerDirection, ...] = (
            OwnerDirection.LONG,
            OwnerDirection.SHORT,
        ),
    ) -> tuple[OwnerIdentity, ...]:
        """Atomically register the mutually exclusive owners of one attack.

        The configured new-identity mass is allocated once and split equally,
        so neither mapping/iteration order nor direction receives a prior
        advantage.  Repeated registration advances the same tracks.
        """

        self._validate_time(time_ns)
        if not source_id:
            raise LatentOwnerError("source_id cannot be empty")
        if generation < 1:
            raise LatentOwnerError("generation must be positive")
        normalized = tuple(dict.fromkeys(OwnerDirection(direction) for direction in directions))
        if len(normalized) < 2:
            raise LatentOwnerError("a competing attack needs at least two distinct directions")
        identities = tuple(
            sorted(OwnerIdentity(source_id, generation, direction) for direction in normalized)
        )
        present = tuple(identity in self._states for identity in identities)
        if any(present) and not all(present):
            raise DirectOwnerFlipError(
                "a sole committed identity cannot be expanded into a competing pair"
            )
        if all(present):
            for identity in identities:
                state = self._states[identity]
                state.attack_count += 1
                state.last_attack_time_ns = time_ns
                self._reset_attack_phase(state)
        else:
            self._add_identities_equal(identities, time_ns)
        self._last_event_time_ns = time_ns
        return identities

    def update(self, identity: OwnerIdentity, observation: SourceObservation) -> PosteriorView:
        """Apply one causal source observation and return the filtered posterior."""

        return self.update_competing({identity: observation})

    def update_competing(
        self, observations: Mapping[OwnerIdentity, SourceObservation]
    ) -> PosteriorView:
        """Filter one completed bar across every active identity exactly once.

        Each identity evaluates its own source-aligned transform of the same
        bar.  The identity likelihoods and one NONE likelihood are normalized
        together, avoiding duplicated price/flow evidence and mapping-order
        dependence.  Missing identity observations are marginalized.
        """

        if not observations:
            raise LatentOwnerError("a competing update needs at least one source observation")
        unknown = set(observations).difference(self._states)
        if unknown:
            raise LatentOwnerError("observation identity is not active")
        times = {observation.time_ns for observation in observations.values()}
        if len(times) != 1:
            raise LatentOwnerError("competing source observations must describe the same bar")
        time_ns = next(iter(times))
        self._validate_time(time_ns)
        if self._last_update_time_ns is not None and time_ns <= self._last_update_time_ns:
            raise LatentOwnerError("a completed bar can update the owner posterior only once")

        identity_log_likelihood: dict[OwnerIdentity, float] = {}
        none_candidates: list[float] = []
        for key in sorted(self._states):
            state = self._states[key]
            if state.last_observation_time_ns is not None and time_ns <= state.last_observation_time_ns:
                raise LatentOwnerError("identity observations must advance by completed bar")
            observation = observations.get(key)
            available = () if observation is None else observation.available()
            # An absent transform marginalizes to likelihood one while the
            # known observation step still advances explicit-duration phases.
            identity_log_likelihood[key] = self._imm_predict_and_update(state, available)
            if observation is not None:
                none_candidates.append(
                    sum(
                        _normal_log_density(value, 0.0, self.config.none_noise_variance)
                        for _, value in available
                    )
                )
            state.last_observation_time_ns = time_ns

        # The mappings are alternative source-coordinate views of one bar, not
        # independent samples.  NONE is therefore a mixture evaluated once,
        # never a product that double-counts the bar.
        none_log_likelihood = (
            _logsumexp(none_candidates) - math.log(len(none_candidates))
            if none_candidates
            else 0.0
        )
        log_weights: dict[OwnerIdentity | None, float] = {
            None: math.log(max(self._none_probability, self.config.minimum_probability))
            + none_log_likelihood
        }
        for key in sorted(self._states):
            log_weights[key] = (
                math.log(max(self._states[key].probability, self.config.minimum_probability))
                + identity_log_likelihood[key]
            )
        normalizer = _logsumexp(list(log_weights.values()))
        self._none_probability = math.exp(log_weights[None] - normalizer)
        for key, candidate in self._states.items():
            candidate.probability = math.exp(log_weights[key] - normalizer)

        self._last_event_time_ns = time_ns
        self._last_update_time_ns = time_ns
        return self.posterior()

    def mark_target_consumed(self, identity: OwnerIdentity, time_ns: int, target_id: str) -> TerminalRecord:
        return self._terminate(identity, time_ns, TerminalReason.TARGET_CONSUMED, target_id)

    def mark_structurally_invalidated(
        self, identity: OwnerIdentity, time_ns: int, invalidation_id: str
    ) -> TerminalRecord:
        return self._terminate(identity, time_ns, TerminalReason.STRUCTURAL_INVALIDATION, invalidation_id)

    def supersede(
        self,
        identity: OwnerIdentity,
        replacement: OwnerIdentity,
        time_ns: int,
        structure_id: str,
        observation: SourceObservation | None = None,
    ) -> TerminalRecord:
        """Atomically terminate one identity and introduce its structural replacement."""

        if identity == replacement:
            raise LatentOwnerError("an identity cannot supersede itself")
        record = self._terminate(
            identity,
            time_ns,
            TerminalReason.EXPLICIT_SUPERSESSION,
            structure_id,
            replacement=replacement,
        )
        self.register_attack(replacement, time_ns, observation)
        return record

    def posterior(self) -> PosteriorView:
        identities = {key: self._states[key].probability for key in sorted(self._states)}
        phase = {
            key: {model: self._states[key].phase_probability[model] for model in OwnerPhase}
            for key in sorted(self._states)
        }
        joint = {
            key: {model: identities[key] * phase[key][model] for model in OwnerPhase}
            for key in sorted(self._states)
        }
        probabilities = [self._none_probability, *identities.values()]
        entropy = -sum(value * math.log(value) for value in probabilities if value > 0.0)
        return PosteriorView(self._none_probability, identities, phase, joint, entropy)

    def attack_count(self, identity: OwnerIdentity) -> int:
        try:
            return self._states[identity].attack_count
        except KeyError as exc:
            raise LatentOwnerError("identity is not active") from exc

    @property
    def terminal_records(self) -> tuple[TerminalRecord, ...]:
        return tuple(self._terminal)

    def export_state(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible snapshot."""

        states: list[dict[str, Any]] = []
        for key in sorted(self._states):
            state = self._states[key]
            states.append(
                {
                    "identity": key.token,
                    "probability": state.probability,
                    "attack_count": state.attack_count,
                    "last_attack_time_ns": state.last_attack_time_ns,
                    "last_observation_time_ns": state.last_observation_time_ns,
                    "phase_probability": {
                        phase.value: state.phase_probability[phase] for phase in OwnerPhase
                    },
                    "mode": {
                        phase.value: {
                            "mean": state.mode[phase].mean,
                            "variance": state.mode[phase].variance,
                            "expected_age": state.mode[phase].expected_age,
                        }
                        for phase in OwnerPhase
                    },
                }
            )
        return {
            "version": self.SNAPSHOT_VERSION,
            "none_probability": self._none_probability,
            "last_event_time_ns": self._last_event_time_ns,
            "last_update_time_ns": self._last_update_time_ns,
            "states": states,
            "terminal": [
                {
                    "identity": record.identity.token,
                    "reason": record.reason.value,
                    "structure_id": record.structure_id,
                    "time_ns": record.time_ns,
                    "replacement": record.replacement.token if record.replacement else None,
                }
                for record in self._terminal
            ],
        }

    def restore_state(self, snapshot: Mapping[str, Any]) -> None:
        """Restore a snapshot produced by :meth:`export_state`."""

        if int(snapshot.get("version", -1)) != self.SNAPSHOT_VERSION:
            raise LatentOwnerError("unsupported latent-owner snapshot version")
        states: dict[OwnerIdentity, _IdentityState] = {}
        for raw in snapshot.get("states", []):
            key = OwnerIdentity.from_token(str(raw["identity"]))
            phase_probability = {
                phase: float(raw["phase_probability"][phase.value]) for phase in OwnerPhase
            }
            mode = {
                phase: _ModeState(
                    mean=float(raw["mode"][phase.value]["mean"]),
                    variance=float(raw["mode"][phase.value]["variance"]),
                    expected_age=float(raw["mode"][phase.value]["expected_age"]),
                )
                for phase in OwnerPhase
            }
            states[key] = _IdentityState(
                key=key,
                probability=float(raw["probability"]),
                phase_probability=phase_probability,
                mode=mode,
                attack_count=int(raw["attack_count"]),
                last_attack_time_ns=int(raw["last_attack_time_ns"]),
                last_observation_time_ns=(
                    None
                    if raw.get("last_observation_time_ns") is None
                    else int(raw["last_observation_time_ns"])
                ),
            )
        terminal = [
            TerminalRecord(
                identity=OwnerIdentity.from_token(str(raw["identity"])),
                reason=TerminalReason(str(raw["reason"])),
                structure_id=str(raw["structure_id"]),
                time_ns=int(raw["time_ns"]),
                replacement=(
                    None
                    if raw.get("replacement") is None
                    else OwnerIdentity.from_token(str(raw["replacement"]))
                ),
            )
            for raw in snapshot.get("terminal", [])
        ]
        none_probability = float(snapshot["none_probability"])
        total = none_probability + sum(state.probability for state in states.values())
        if not math.isfinite(total) or abs(total - 1.0) > 1e-9:
            raise LatentOwnerError("snapshot categorical posterior must sum to one")
        if len(states) != len(snapshot.get("states", [])):
            raise LatentOwnerError("snapshot contains duplicate identities")
        self._states = states
        self._terminal = terminal
        self._none_probability = none_probability
        last_time = snapshot.get("last_event_time_ns")
        self._last_event_time_ns = None if last_time is None else int(last_time)
        last_update = snapshot.get("last_update_time_ns")
        self._last_update_time_ns = None if last_update is None else int(last_update)

    @classmethod
    def from_state(
        cls, snapshot: Mapping[str, Any], config: LatentOwnerConfig | None = None
    ) -> "LatentOwnerFilter":
        result = cls(config)
        result.restore_state(snapshot)
        return result

    def canonical_snapshot(self) -> str:
        """Stable byte-for-byte representation useful for checkpoints/hashes."""

        return json.dumps(self.export_state(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    def _add_identity(self, identity: OwnerIdentity, time_ns: int) -> None:
        self._add_identities_equal((identity,), time_ns)

    def _reset_attack_phase(self, state: _IdentityState) -> None:
        """Begin a new contest without erasing source-owner identity memory."""

        total = sum(self.config.initial_phase_probability.values())
        state.phase_probability = {
            phase: float(self.config.initial_phase_probability[phase]) / total
            for phase in OwnerPhase
        }
        for mode in state.mode.values():
            mode.expected_age = 1.0

    def _add_identities_equal(
        self, identities: tuple[OwnerIdentity, ...], time_ns: int
    ) -> None:
        if not identities:
            raise LatentOwnerError("at least one identity is required")
        allocation = self.config.new_identity_mass
        self._none_probability *= 1.0 - allocation
        for state in self._states.values():
            state.probability *= 1.0 - allocation
        initial_total = sum(self.config.initial_phase_probability.values())
        phase_probability = {
            phase: float(self.config.initial_phase_probability[phase]) / initial_total for phase in OwnerPhase
        }
        per_identity = allocation / len(identities)
        for identity in identities:
            self._states[identity] = _IdentityState(
                key=identity,
                probability=per_identity,
                phase_probability=dict(phase_probability),
                mode={
                    phase: _ModeState(
                        self.config.initial_latent_mean,
                        self.config.initial_latent_variance,
                        1.0,
                    )
                    for phase in OwnerPhase
                },
                attack_count=1,
                last_attack_time_ns=time_ns,
            )

    def _imm_predict_and_update(
        self, state: _IdentityState, available: tuple[tuple[str, float], ...]
    ) -> float:
        previous_probability = dict(state.phase_probability)
        previous_mode = {
            phase: _ModeState(
                state.mode[phase].mean,
                state.mode[phase].variance,
                state.mode[phase].expected_age,
            )
            for phase in OwnerPhase
        }

        transition: dict[OwnerPhase, dict[OwnerPhase, float]] = {}
        for source in OwnerPhase:
            hazard = self._duration_hazard(source, previous_mode[source].expected_age)
            row = {destination: 0.0 for destination in OwnerPhase}
            row[source] = 1.0 - hazard
            for destination, weight in _DESTINATION_WEIGHTS[source].items():
                row[destination] += hazard * weight
            transition[source] = row

        predicted_probability: dict[OwnerPhase, float] = {}
        predicted_mode: dict[OwnerPhase, _ModeState] = {}
        for destination in OwnerPhase:
            contributions = {
                source: previous_probability[source] * transition[source][destination]
                for source in OwnerPhase
            }
            total = sum(contributions.values())
            predicted_probability[destination] = total
            if total <= self.config.minimum_probability:
                predicted_mode[destination] = _ModeState(
                    self.config.initial_latent_mean,
                    self.config.initial_latent_variance,
                    1.0,
                )
                continue
            weights = {source: value / total for source, value in contributions.items()}
            mean = sum(weights[source] * previous_mode[source].mean for source in OwnerPhase)
            variance = sum(
                weights[source]
                * (
                    previous_mode[source].variance
                    + (previous_mode[source].mean - mean) ** 2
                )
                for source in OwnerPhase
            )
            age = sum(
                weights[source]
                * (previous_mode[source].expected_age + 1.0 if source is destination else 1.0)
                for source in OwnerPhase
            )
            predicted_mode[destination] = _ModeState(mean, variance, age)

        log_likelihood: dict[OwnerPhase, float] = {}
        updated_mode: dict[OwnerPhase, _ModeState] = {}
        for phase in OwnerPhase:
            prior = self.config.phase_priors[phase]
            mean = predicted_mode[phase].mean
            variance = max(
                predicted_mode[phase].variance + prior.process_variance,
                self.config.minimum_variance,
            )
            phase_log_likelihood = 0.0
            # Sequential scalar Kalman innovations exactly factor the joint
            # likelihood for conditionally independent emissions sharing x.
            for name, value in available:
                loading = float(prior.loading[name])
                observation_variance = max(
                    float(prior.noise_variance[name]), self.config.minimum_variance
                )
                innovation_variance = loading * loading * variance + observation_variance
                predicted_observation = float(prior.bias[name]) + loading * mean
                phase_log_likelihood += _normal_log_density(
                    value, predicted_observation, innovation_variance
                )
                gain = variance * loading / innovation_variance
                mean += gain * (value - predicted_observation)
                variance = max(
                    (1.0 - gain * loading) * variance,
                    self.config.minimum_variance,
                )
            log_likelihood[phase] = phase_log_likelihood
            updated_mode[phase] = _ModeState(mean, variance, predicted_mode[phase].expected_age)

        model_log_weights = {
            phase: math.log(max(predicted_probability[phase], self.config.minimum_probability))
            + log_likelihood[phase]
            for phase in OwnerPhase
        }
        model_normalizer = _logsumexp(list(model_log_weights.values()))
        state.phase_probability = {
            phase: math.exp(model_log_weights[phase] - model_normalizer) for phase in OwnerPhase
        }
        state.mode = updated_mode
        # This is the identity model evidence before the phase posterior is
        # normalized, as required by the outer categorical MMAE update.
        return model_normalizer

    def _duration_hazard(self, phase: OwnerPhase, age: float) -> float:
        prior = self.config.phase_priors[phase]
        age = max(float(age), 0.0)
        scale = max(float(prior.duration_scale), 1e-6)
        shape = max(float(prior.duration_shape), 1e-6)
        cumulative_increment = ((age + 1.0) / scale) ** shape - (age / scale) ** shape
        # A phase transition remains probabilistic at every duration.  It never
        # expires or terminates an owner identity.
        return min(max(1.0 - math.exp(-cumulative_increment), 1e-6), 1.0 - 1e-6)

    def _terminate(
        self,
        identity: OwnerIdentity,
        time_ns: int,
        reason: TerminalReason,
        structure_id: str,
        replacement: OwnerIdentity | None = None,
    ) -> TerminalRecord:
        self._validate_time(time_ns)
        if not structure_id:
            raise LatentOwnerError("terminal structure id cannot be empty")
        try:
            state = self._states.pop(identity)
        except KeyError as exc:
            raise LatentOwnerError("terminal identity is not active") from exc
        # Structural termination explicitly passes through NONE.  This is a
        # categorical probability transfer, never a moment merge.
        self._none_probability += state.probability
        record = TerminalRecord(identity, reason, structure_id, time_ns, replacement)
        self._terminal.append(record)
        self._last_event_time_ns = time_ns
        return record

    def _validate_time(self, time_ns: int) -> None:
        if time_ns < 0:
            raise LatentOwnerError("event time cannot be negative")
        if self._last_event_time_ns is not None and time_ns < self._last_event_time_ns:
            raise LatentOwnerError("events must be processed causally")


__all__ = [
    "DirectOwnerFlipError",
    "LatentOwnerConfig",
    "LatentOwnerError",
    "LatentOwnerFilter",
    "OwnerDirection",
    "OwnerIdentity",
    "OwnerPhase",
    "PhasePrior",
    "PosteriorView",
    "SourceObservation",
    "TerminalReason",
    "TerminalRecord",
]
