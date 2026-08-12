import unittest

from role_graph_v15 import (
    Direction,
    EvidenceKind,
    EvidenceRole,
    LocalPhase,
    PositioningContext,
    PositioningState,
    Scale,
    ScenarioFamily,
    evidence,
    resolve_option,
)


class PositioningHierarchyTests(unittest.TestCase):
    def test_local_opposite_footprint_is_pullback_not_macro_flip(self):
        macro = evidence(
            "case33-daily-long",
            kind=EvidenceKind.SWING_STRUCTURE,
            roles={EvidenceRole.DIRECTION, EvidenceRole.INVALIDATION},
            direction=Direction.LONG,
            scale=Scale.MACRO,
            event_time_ns=10,
        )
        context = PositioningContext().establish(
            Direction.LONG,
            evidence=macro,
            invalidation_price=70_500.0,
        )
        local_bearish_ob = evidence(
            "case33-local-bearish-ob",
            kind=EvidenceKind.ORDER_BLOCK,
            roles={EvidenceRole.LOCATION, EvidenceRole.RESPONSE},
            direction=Direction.SHORT,
            scale=Scale.LOCAL,
            event_time_ns=20,
        )
        updated = context.apply_local(local_bearish_ob)
        self.assertEqual(updated.state, PositioningState.LONG)
        self.assertEqual(updated.local_phase, LocalPhase.PULLBACK)

    def test_only_same_or_higher_scale_explicit_invalidation_clears_macro(self):
        macro = evidence(
            "macro-long",
            kind=EvidenceKind.SWING_STRUCTURE,
            roles={EvidenceRole.DIRECTION},
            direction=Direction.LONG,
            scale=Scale.MACRO,
            event_time_ns=10,
        )
        context = PositioningContext().establish(
            Direction.LONG,
            evidence=macro,
            invalidation_price=100.0,
        )
        low_scale = evidence(
            "local-invalidation",
            kind=EvidenceKind.MACRO_INVALIDATION,
            roles={EvidenceRole.DIRECTION},
            direction=Direction.SHORT,
            scale=Scale.LOCAL,
            event_time_ns=20,
        )
        with self.assertRaises(ValueError):
            context.apply_local(low_scale)
        macro_break = evidence(
            "macro-invalidation",
            kind=EvidenceKind.MACRO_INVALIDATION,
            roles={EvidenceRole.DIRECTION},
            direction=Direction.SHORT,
            scale=Scale.MACRO,
            event_time_ns=30,
        )
        cleared = context.apply_local(macro_break)
        self.assertEqual(cleared.state, PositioningState.UNRESOLVED)


class MinimalSufficientOptionTests(unittest.TestCase):
    @staticmethod
    def _base_case02():
        return [
            evidence(
                "trendline",
                kind=EvidenceKind.TRENDLINE,
                roles={EvidenceRole.LOCATION},
                direction=Direction.LONG,
                scale=Scale.CONTEXT,
                event_time_ns=10,
            ),
            evidence(
                "accepted-break",
                kind=EvidenceKind.ACCEPTED_BREAK,
                roles={EvidenceRole.INTERACTION, EvidenceRole.RESPONSE},
                direction=Direction.LONG,
                scale=Scale.LOCAL,
                event_time_ns=20,
            ),
            evidence(
                "bullish-ob",
                kind=EvidenceKind.ORDER_BLOCK,
                roles={EvidenceRole.ENTRY, EvidenceRole.INVALIDATION},
                direction=Direction.LONG,
                scale=Scale.EXECUTION,
                event_time_ns=25,
            ),
            evidence(
                "prior-high",
                kind=EvidenceKind.OPPOSING_STRUCTURE,
                roles={EvidenceRole.OBJECTIVE},
                direction=Direction.LONG,
                scale=Scale.LOCAL,
                event_time_ns=15,
            ),
        ]

    def test_case02_is_complete_without_fvg_or_channel(self):
        result = resolve_option(
            family=ScenarioFamily.ACCEPTED_BREAK_FIRST_RETEST,
            direction=Direction.LONG,
            evidence=self._base_case02(),
            context=PositioningContext(),
        )
        self.assertTrue(result.executable)
        self.assertEqual(result.disposition, "EXECUTABLE")

    def test_repeated_synonyms_do_not_replace_a_missing_role(self):
        items = self._base_case02()
        items = [
            item
            for item in items
            if EvidenceRole.OBJECTIVE not in item.roles
        ]
        items.extend(
            [
                evidence(
                    f"extra-response-{i}",
                    kind=EvidenceKind.DISPLACEMENT,
                    roles={EvidenceRole.RESPONSE},
                    direction=Direction.LONG,
                    scale=Scale.EXECUTION,
                    event_time_ns=26 + i,
                    causal_leg_id="same-displacement",
                )
                for i in range(5)
            ]
        )
        result = resolve_option(
            family=ScenarioFamily.ACCEPTED_BREAK_FIRST_RETEST,
            direction=Direction.LONG,
            evidence=items,
            context=PositioningContext(),
        )
        self.assertFalse(result.executable)
        self.assertIn("OBJECTIVE", result.disposition)

    def test_case37_predictive_outside_footprint_can_be_complete(self):
        items = [
            evidence(
                "channel-lower",
                kind=EvidenceKind.CHANNEL,
                roles={EvidenceRole.LOCATION, EvidenceRole.OBJECTIVE},
                direction=Direction.LONG,
                scale=Scale.CONTEXT,
                event_time_ns=10,
            ),
            evidence(
                "outside-test",
                kind=EvidenceKind.LIQUIDITY_SWEEP,
                roles={EvidenceRole.INTERACTION},
                direction=Direction.LONG,
                scale=Scale.LOCAL,
                event_time_ns=20,
            ),
            evidence(
                "4h-bullish-ob",
                kind=EvidenceKind.ORDER_BLOCK,
                roles={
                    EvidenceRole.RESPONSE,
                    EvidenceRole.ENTRY,
                    EvidenceRole.INVALIDATION,
                },
                direction=Direction.LONG,
                scale=Scale.LOCAL,
                event_time_ns=30,
            ),
        ]
        result = resolve_option(
            family=ScenarioFamily.FAILED_BREAK_REVERSAL,
            direction=Direction.LONG,
            evidence=items,
            context=PositioningContext(),
        )
        self.assertTrue(result.executable)

    def test_context_pullback_continuation_requires_matching_macro_direction(self):
        items = [
            evidence(
                "macro-long",
                kind=EvidenceKind.SWING_STRUCTURE,
                roles={EvidenceRole.DIRECTION, EvidenceRole.INVALIDATION},
                direction=Direction.LONG,
                scale=Scale.MACRO,
                event_time_ns=5,
            ),
            evidence(
                "daily-demand",
                kind=EvidenceKind.ORDER_BLOCK,
                roles={EvidenceRole.LOCATION},
                direction=Direction.LONG,
                scale=Scale.CONTEXT,
                event_time_ns=10,
            ),
            evidence(
                "pullback",
                kind=EvidenceKind.ORDER_BLOCK,
                roles={EvidenceRole.INTERACTION},
                direction=Direction.LONG,
                scale=Scale.LOCAL,
                event_time_ns=20,
            ),
            evidence(
                "support-response",
                kind=EvidenceKind.FVG,
                roles={EvidenceRole.RESPONSE, EvidenceRole.ENTRY},
                direction=Direction.LONG,
                scale=Scale.LOCAL,
                event_time_ns=30,
            ),
            evidence(
                "macro-target",
                kind=EvidenceKind.OPPOSING_STRUCTURE,
                roles={EvidenceRole.OBJECTIVE},
                direction=Direction.LONG,
                scale=Scale.CONTEXT,
                event_time_ns=15,
            ),
        ]
        macro_evidence = items[0]
        long_context = PositioningContext().establish(
            Direction.LONG,
            evidence=macro_evidence,
            invalidation_price=70_500.0,
        )
        accepted = resolve_option(
            family=ScenarioFamily.DOMINANT_CONTEXT_PULLBACK_CONTINUATION,
            direction=Direction.LONG,
            evidence=items,
            context=long_context,
        )
        self.assertTrue(accepted.executable)
        short_context = PositioningContext().establish(
            Direction.SHORT,
            evidence=evidence(
                "macro-short",
                kind=EvidenceKind.SWING_STRUCTURE,
                roles={EvidenceRole.DIRECTION},
                direction=Direction.SHORT,
                scale=Scale.MACRO,
                event_time_ns=5,
            ),
            invalidation_price=80_000.0,
        )
        rejected = resolve_option(
            family=ScenarioFamily.DOMINANT_CONTEXT_PULLBACK_CONTINUATION,
            direction=Direction.LONG,
            evidence=items,
            context=short_context,
        )
        self.assertFalse(rejected.executable)
        self.assertEqual(rejected.disposition, "DIRECTIONAL_CONTEXT_MISMATCH")

    def test_entry_cannot_precede_observable_response(self):
        items = self._base_case02()
        items = [
            evidence(
                item.evidence_id,
                kind=item.kind,
                roles=item.roles,
                direction=item.direction,
                scale=item.scale,
                event_time_ns=item.event_time_ns,
                observed_time_ns=30 if EvidenceRole.RESPONSE in item.roles else item.observed_time_ns,
                causal_leg_id=item.causal_leg_id,
            )
            if EvidenceRole.RESPONSE in item.roles
            else item
            for item in items
        ]
        result = resolve_option(
            family=ScenarioFamily.ACCEPTED_BREAK_FIRST_RETEST,
            direction=Direction.LONG,
            evidence=items,
            context=PositioningContext(),
        )
        self.assertFalse(result.executable)
        self.assertEqual(result.disposition, "ENTRY_PRECEDES_RESPONSE")


if __name__ == "__main__":
    unittest.main()
