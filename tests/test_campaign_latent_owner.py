from __future__ import annotations

import json
import math

import pytest

from smc_ict_4.campaign_policy.latent_owner import (
    DirectOwnerFlipError,
    LatentOwnerError,
    LatentOwnerFilter,
    OwnerDirection,
    OwnerIdentity,
    OwnerPhase,
    SourceObservation,
    TerminalReason,
)


def _identity(source: str, direction: OwnerDirection = OwnerDirection.LONG) -> OwnerIdentity:
    return OwnerIdentity(source, 1, direction)


def _delivery_observation(time_ns: int) -> SourceObservation:
    return SourceObservation(
        time_ns=time_ns,
        return_progress=0.9,
        source_progress=0.8,
        spot_flow=0.7,
        perp_flow=0.4,
        impact_per_flow=0.6,
        distance_from_source=0.7,
        target_progress=0.5,
        common_nuisance=0.1,
        residual_return=0.9,
    )


def test_source_identities_compete_without_moment_merge() -> None:
    owner_a = _identity("btc:prior-day-low")
    owner_b = _identity("btc:weekly-balance-low")
    owner_filter = LatentOwnerFilter()
    owner_filter.register_attack(owner_a, 1)
    owner_filter.register_attack(owner_b, 2)

    control = LatentOwnerFilter.from_state(owner_filter.export_state())
    owner_filter.update(owner_a, _delivery_observation(3))
    control.update(owner_a, SourceObservation(time_ns=3, residual_return=-1.5))
    after = owner_filter.export_state()
    control_after = control.export_state()
    b_mode_after = next(row for row in after["states"] if row["identity"] == owner_b.token)["mode"]
    b_mode_control = next(
        row for row in control_after["states"] if row["identity"] == owner_b.token
    )["mode"]

    posterior = owner_filter.posterior()
    assert set(posterior.identity_probability) == {owner_a, owner_b}
    assert b_mode_after == b_mode_control
    assert posterior.identity_probability[owner_a] != posterior.identity_probability[owner_b]
    assert math.isclose(
        posterior.none_probability + sum(posterior.identity_probability.values()), 1.0
    )


def test_repeated_attack_updates_the_same_identity_track() -> None:
    identity = _identity("eth:external-high", OwnerDirection.SHORT)
    owner_filter = LatentOwnerFilter()

    returned = owner_filter.register_attack(identity, 10)
    owner_filter.register_attack(identity, 11, SourceObservation(time_ns=11, spot_flow=0.5))

    assert returned is identity
    assert owner_filter.attack_count(identity) == 2
    assert tuple(owner_filter.posterior().identity_probability) == (identity,)


def test_missing_observation_dimensions_are_marginalized() -> None:
    identity = _identity("sol:balance-low")
    owner_filter = LatentOwnerFilter()
    owner_filter.register_attack(identity, 1)
    categorical_before = owner_filter.posterior().identity_probability[identity]

    posterior = owner_filter.update(identity, SourceObservation(time_ns=2))

    assert posterior.identity_probability[identity] == pytest.approx(categorical_before)
    assert sum(posterior.phase_probability[identity].values()) == pytest.approx(1.0)
    assert all(math.isfinite(value) for value in posterior.phase_probability[identity].values())


def test_optional_asof_dimensions_distinguish_missing_from_observed_zero() -> None:
    missing = SourceObservation(time_ns=1)
    observed_zero = SourceObservation(
        time_ns=1,
        open_interest_change=0.0,
        basis_change=0.0,
        depth_imbalance=0.0,
    )

    assert missing.available() == ()
    assert dict(observed_zero.available()) == {
        "open_interest_change": 0.0,
        "basis_change": 0.0,
        "depth_imbalance": 0.0,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open_interest_change", math.nan),
        ("basis_change", math.inf),
        ("depth_imbalance", -math.inf),
    ],
)
def test_optional_asof_dimensions_must_be_finite_when_present(
    field: str, value: float
) -> None:
    with pytest.raises(LatentOwnerError, match=field):
        SourceObservation(time_ns=1, **{field: value})


def test_filter_is_prefix_invariant() -> None:
    identity = _identity("xrp:prior-day-high", OwnerDirection.SHORT)
    prefix = [
        SourceObservation(time_ns=2, source_progress=0.1, spot_flow=-0.2),
        SourceObservation(time_ns=3, return_progress=0.3, residual_return=0.2),
    ]
    left = LatentOwnerFilter()
    right = LatentOwnerFilter()
    left.register_attack(identity, 1)
    right.register_attack(identity, 1)
    for observation in prefix:
        left.update(identity, observation)
        right.update(identity, observation)

    frozen_prefix = left.canonical_snapshot()
    assert frozen_prefix == right.canonical_snapshot()

    right.update(identity, _delivery_observation(4))
    assert left.canonical_snapshot() == frozen_prefix
    assert right.canonical_snapshot() != frozen_prefix


def test_structural_terminal_enters_none_and_blocks_direct_flip() -> None:
    long_owner = _identity("btc:external-low", OwnerDirection.LONG)
    short_owner = _identity("btc:external-low", OwnerDirection.SHORT)
    owner_filter = LatentOwnerFilter()
    owner_filter.register_attack(long_owner, 1)

    with pytest.raises(DirectOwnerFlipError):
        owner_filter.register_attack(short_owner, 2)

    record = owner_filter.mark_target_consumed(long_owner, 3, "btc:paired-high")
    assert record.reason is TerminalReason.TARGET_CONSUMED
    assert owner_filter.posterior().none_probability == pytest.approx(1.0)
    owner_filter.register_attack(short_owner, 4)
    assert short_owner in owner_filter.posterior().identity_probability


def test_competing_attack_registers_equal_prior_without_direction_order_bias() -> None:
    owner_filter = LatentOwnerFilter()
    identities = owner_filter.register_competing_attack(
        "btc:external-low",
        1,
        1,
        directions=(OwnerDirection.SHORT, OwnerDirection.LONG),
    )

    posterior = owner_filter.posterior()
    assert len(identities) == 2
    assert posterior.identity_probability[identities[0]] == pytest.approx(
        posterior.identity_probability[identities[1]]
    )
    assert sum(posterior.identity_probability.values()) == pytest.approx(0.16)
    assert posterior.none_probability == pytest.approx(0.84)
    owner_filter.register_competing_attack("btc:external-low", 1, 2)
    assert all(owner_filter.attack_count(identity) == 2 for identity in identities)


def test_reattack_preserves_owner_mass_but_starts_a_new_contest_phase() -> None:
    owner_filter = LatentOwnerFilter()
    identities = owner_filter.register_competing_attack("btc:day-high", 1, 1)
    for time_ns in range(2, 7):
        owner_filter.update_competing(
            {identity: _delivery_observation(time_ns) for identity in identities}
        )
    before = owner_filter.posterior()

    owner_filter.register_competing_attack("btc:day-high", 1, 7)
    after = owner_filter.posterior()

    assert after.identity_probability == pytest.approx(before.identity_probability)
    for identity in identities:
        assert after.phase_probability[identity][OwnerPhase.CONTEST] == pytest.approx(0.82)


def test_competing_update_is_mapping_order_invariant() -> None:
    left = LatentOwnerFilter()
    right = LatentOwnerFilter()
    identities = left.register_competing_attack("eth:day-low", 1, 1)
    right.register_competing_attack("eth:day-low", 1, 1)
    long_owner = next(key for key in identities if key.direction is OwnerDirection.LONG)
    short_owner = next(key for key in identities if key.direction is OwnerDirection.SHORT)
    long_observation = SourceObservation(
        time_ns=2, return_progress=0.8, spot_flow=0.5, residual_return=0.7
    )
    short_observation = SourceObservation(
        time_ns=2, return_progress=-0.8, spot_flow=-0.5, residual_return=-0.7
    )

    left.update_competing(
        {long_owner: long_observation, short_owner: short_observation}
    )
    right.update_competing(
        {short_owner: short_observation, long_owner: long_observation}
    )

    assert left.canonical_snapshot() == right.canonical_snapshot()


def test_completed_bar_evidence_can_be_applied_only_once() -> None:
    owner_filter = LatentOwnerFilter()
    identities = owner_filter.register_competing_attack("sol:week-high", 1, 1)
    observations = {
        identity: SourceObservation(time_ns=2, source_progress=0.2)
        for identity in identities
    }
    posterior = owner_filter.update_competing(observations)

    assert posterior.none_probability + sum(posterior.identity_probability.values()) == pytest.approx(1.0)
    with pytest.raises(LatentOwnerError, match="only once"):
        owner_filter.update(identities[0], observations[identities[0]])


def test_invalidation_and_supersession_are_exact_structural_terminals() -> None:
    first = _identity("eth:balance-low")
    replacement = OwnerIdentity("eth:child-balance-low", 2, OwnerDirection.LONG)
    owner_filter = LatentOwnerFilter()
    owner_filter.register_attack(first, 1)
    record = owner_filter.supersede(first, replacement, 2, "parent-rebalanced")

    assert record.reason is TerminalReason.EXPLICIT_SUPERSESSION
    assert record.replacement == replacement
    assert first not in owner_filter.posterior().identity_probability
    assert replacement in owner_filter.posterior().identity_probability

    invalidated = owner_filter.mark_structurally_invalidated(replacement, 3, "child-balance-broken")
    assert invalidated.reason is TerminalReason.STRUCTURAL_INVALIDATION
    with pytest.raises(LatentOwnerError):
        owner_filter.update(replacement, SourceObservation(time_ns=4, return_progress=0.0))


def test_snapshot_round_trip_is_deterministic() -> None:
    owner_a = _identity("btc:day-low")
    owner_b = _identity("eth:day-high", OwnerDirection.SHORT)
    owner_filter = LatentOwnerFilter()
    owner_filter.register_attack(owner_a, 1)
    owner_filter.update(owner_a, SourceObservation(time_ns=2, spot_flow=0.3, common_nuisance=0.8))
    owner_filter.register_attack(owner_b, 3)
    owner_filter.update(owner_b, _delivery_observation(4))

    snapshot = owner_filter.export_state()
    restored = LatentOwnerFilter.from_state(json.loads(json.dumps(snapshot)))

    assert restored.canonical_snapshot() == owner_filter.canonical_snapshot()
    assert restored.posterior().entropy == pytest.approx(owner_filter.posterior().entropy)
    assert set(restored.posterior().phase_probability[owner_a]) == set(OwnerPhase)
