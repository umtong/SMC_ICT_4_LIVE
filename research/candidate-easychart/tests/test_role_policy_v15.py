from __future__ import annotations

import unittest

from domain_v3 import ArmedSetup, Side, TargetMode
from role_policy_v15 import (
    EntryZone,
    EpisodeHypothesis,
    ObjectiveCandidate,
    ObservationKind,
    PolicyDecision,
    Role,
    RoleEvidence,
    RuleOrigin,
    choose_first_objective,
    decide_episode,
    duplicate_evidence_groups,
    resolve_competing_decisions,
)


class RolePolicyV15Test(unittest.TestCase):
    def evidence(
        self,
        evidence_id: str,
        kind: ObservationKind,
        roles: set[Role],
        *,
        parent: str,
        observed: int = 100,
        side: Side = Side.LONG,
        level: float | None = None,
    ) -> RoleEvidence:
        return RoleEvidence(
            evidence_id=evidence_id,
            kind=kind,
            roles=frozenset(roles),
            origin=RuleOrigin.SOURCE_EXPLICIT,
            observed_time_ns=observed,
            event_time_ns=observed - 1,
            side=side,
            level=level,
            parent_event_id=parent,
        )

    def complete_evidence(self, episode_id: str = "episode-1") -> tuple[RoleEvidence, ...]:
        return (
            self.evidence(
                "context",
                ObservationKind.TRENDLINE,
                {Role.CONTEXT},
                parent="context-leg",
                level=100.0,
            ),
            self.evidence(
                "liquidity",
                ObservationKind.RANGE_LOW,
                {Role.LIQUIDITY},
                parent="range-1",
                level=100.0,
            ),
            self.evidence(
                "interaction",
                ObservationKind.IMMEDIATE_FAKEOUT,
                {Role.INTERACTION},
                parent=episode_id,
                level=100.0,
            ),
            self.evidence(
                "reclaim",
                ObservationKind.RECLAIM,
                {Role.STATE_TRANSITION},
                parent=episode_id,
                level=100.0,
            ),
        )

    def objective(
        self,
        objective_id: str,
        level: float,
        *,
        observed: int = 90,
        kind: ObservationKind = ObservationKind.SWING_HIGH,
    ) -> ObjectiveCandidate:
        return ObjectiveCandidate(
            objective_id=objective_id,
            kind=kind,
            level=level,
            observed_time_ns=observed,
            event_time_ns=observed - 1,
            active=True,
        )

    def boundary_zone(self, episode_id: str = "episode-1") -> EntryZone:
        return EntryZone(
            zone_id="reclaimed-range-low",
            kind=ObservationKind.RANGE_LOW,
            side=Side.LONG,
            low=100.0,
            high=100.0,
            invalidation=98.0,
            observed_time_ns=100,
            causal_parent_id=episode_id,
            fresh=True,
        )

    def episode(
        self,
        *,
        episode_id: str = "episode-1",
        entry_zones: tuple[EntryZone, ...] | None = None,
        objectives: tuple[ObjectiveCandidate, ...] | None = None,
        evidence: tuple[RoleEvidence, ...] | None = None,
    ) -> EpisodeHypothesis:
        return EpisodeHypothesis(
            episode_id=episode_id,
            symbol="BTCUSDT",
            family="FAILED_AUCTION_RECLAIM",
            side=Side.LONG,
            observed_time_ns=110,
            interaction_time_ns=100,
            interaction_extreme=97.8,
            evidence=evidence or self.complete_evidence(episode_id),
            entry_zones=entry_zones or (self.boundary_zone(episode_id),),
            objectives=objectives or (self.objective("first-high", 104.0),),
        )

    def test_complete_episode_does_not_require_ob_or_fvg(self) -> None:
        decision = decide_episode(
            self.episode(),
            reference_level=100.0,
            setup_high=101.0,
            setup_low=97.8,
            tick_size=0.1,
            sequence=1,
        )
        self.assertEqual(decision.reason, "ACCEPTED")
        self.assertIsNotNone(decision.setup)
        self.assertEqual(decision.chosen_entry_zone.kind, ObservationKind.RANGE_LOW)
        self.assertEqual(decision.setup.entry, 100.0)
        self.assertEqual(decision.setup.initial_target, 104.0)

    def test_response_ob_is_selected_only_when_it_overlaps_interacted_structure(self) -> None:
        boundary = self.boundary_zone()
        ob = EntryZone(
            zone_id="response-ob",
            kind=ObservationKind.ORDER_BLOCK,
            side=Side.LONG,
            low=99.6,
            high=100.2,
            invalidation=97.9,
            observed_time_ns=105,
            causal_parent_id="episode-1",
            fresh=True,
        )
        decision = decide_episode(
            self.episode(entry_zones=(boundary, ob)),
            reference_level=100.0,
            setup_high=101.0,
            setup_low=97.8,
            tick_size=0.1,
            sequence=2,
        )
        self.assertEqual(decision.reason, "ACCEPTED")
        self.assertEqual(decision.chosen_entry_zone.zone_id, "response-ob")
        self.assertEqual(decision.setup.entry, 100.2)

    def test_same_response_ob_and_fvg_are_recorded_as_one_causal_group(self) -> None:
        evidence = self.complete_evidence() + (
            self.evidence(
                "response-ob",
                ObservationKind.ORDER_BLOCK,
                {Role.ENTRY, Role.INVALIDATION},
                parent="response-leg-1",
            ),
            self.evidence(
                "response-fvg",
                ObservationKind.FVG,
                {Role.STATE_TRANSITION},
                parent="response-leg-1",
            ),
        )
        groups = duplicate_evidence_groups(evidence)
        self.assertIn(("response-fvg", "response-ob"), groups)

    def test_first_active_objective_below_one_r_is_not_skipped(self) -> None:
        episode = self.episode(
            objectives=(
                self.objective("near", 101.0),
                self.objective("far", 108.0),
            ),
        )
        decision = decide_episode(
            episode,
            reference_level=100.0,
            setup_high=100.5,
            setup_low=97.8,
            tick_size=0.1,
            sequence=3,
        )
        self.assertIsNone(decision.setup)
        self.assertEqual(decision.reason, "FIRST_ACTIVE_OBJECTIVE_RR_LT_1")
        self.assertIsNone(decision.chosen_objective)

    def test_conflicting_entry_geometry_is_unresolved(self) -> None:
        first = EntryZone(
            zone_id="ob-a",
            kind=ObservationKind.ORDER_BLOCK,
            side=Side.LONG,
            low=99.0,
            high=99.5,
            invalidation=97.5,
            observed_time_ns=101,
            causal_parent_id="episode-1",
        )
        second = EntryZone(
            zone_id="fvg-b",
            kind=ObservationKind.FVG,
            side=Side.LONG,
            low=100.0,
            high=100.5,
            invalidation=98.0,
            observed_time_ns=102,
            causal_parent_id="episode-1",
        )
        decision = decide_episode(
            self.episode(entry_zones=(first, second)),
            reference_level=None,
            setup_high=101.0,
            setup_low=97.8,
            tick_size=0.1,
            sequence=4,
        )
        self.assertIsNone(decision.setup)
        self.assertEqual(decision.reason, "CONFLICTING_ENTRY_GEOMETRY_UNRESOLVED")

    def test_missing_role_is_not_repaired_by_more_labels(self) -> None:
        evidence = tuple(
            item
            for item in self.complete_evidence()
            if Role.STATE_TRANSITION not in item.roles
        ) + (
            self.evidence(
                "second-interaction-label",
                ObservationKind.IMMEDIATE_FAKEOUT,
                {Role.INTERACTION},
                parent="episode-1",
            ),
        )
        decision = decide_episode(
            self.episode(evidence=evidence),
            reference_level=100.0,
            setup_high=101.0,
            setup_low=97.8,
            tick_size=0.1,
            sequence=5,
        )
        self.assertIsNone(decision.setup)
        self.assertIn("STATE_TRANSITION", decision.reason)

    def test_equivalent_competing_hypotheses_merge_but_different_geometry_does_not(self) -> None:
        first = decide_episode(
            self.episode(episode_id="episode-a"),
            reference_level=100.0,
            setup_high=101.0,
            setup_low=97.8,
            tick_size=0.1,
            sequence=6,
        )
        second = decide_episode(
            self.episode(episode_id="episode-b"),
            reference_level=100.0,
            setup_high=101.0,
            setup_low=97.8,
            tick_size=0.1,
            sequence=7,
        )
        merged, reason = resolve_competing_decisions((first, second))
        self.assertIsNotNone(merged)
        self.assertEqual(reason, "EQUIVALENT_GEOMETRY_MERGED")

        different_setup = ArmedSetup(
            setup_id="different",
            causal_event_id="different",
            symbol="BTCUSDT",
            family="STRUCTURE_FLIP_RETEST",
            side=Side.LONG,
            observed_time_ns=110,
            entry=101.0,
            stop=98.0,
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=106.0,
            fixed_target_id="other",
            source_pool_id="other",
            zone_low=101.0,
            zone_high=101.0,
            formation_extreme=98.0,
            body_ratio=0.0,
        )
        conflict, reason = resolve_competing_decisions(
            (first, PolicyDecision(different_setup, "ACCEPTED")),
        )
        self.assertIsNone(conflict)
        self.assertEqual(reason, "CONFLICTING_HYPOTHESES_UNRESOLVED")

    def test_observation_cannot_precede_event(self) -> None:
        with self.assertRaises(ValueError):
            RoleEvidence(
                evidence_id="future",
                kind=ObservationKind.ORDER_BLOCK,
                roles=frozenset({Role.ENTRY}),
                origin=RuleOrigin.SOURCE_EXPLICIT,
                observed_time_ns=9,
                event_time_ns=10,
            )


if __name__ == "__main__":
    unittest.main()
