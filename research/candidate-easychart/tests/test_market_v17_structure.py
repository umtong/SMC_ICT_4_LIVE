from __future__ import annotations

import unittest

from domain_v3 import Candle, Side
from market_v4 import StructuralPivot
from market_v15 import FootprintRef
from market_v17_structure import (
    CommonCoreAcceptedBreakConfig,
    CommonCoreAcceptedBreakEngine,
    CommonCoreStructureVersion,
    build_common_core_structures,
)


def candle(index: int, o: float, h: float, l: float, c: float) -> Candle:
    start = index * 10
    return Candle(start, start + 9, o, h, l, c, 1.0)


def pivot(
    *,
    center: int,
    observed: int,
    side: str,
    level: float,
    event: int | None = None,
    observed_time: int | None = None,
) -> StructuralPivot:
    return StructuralPivot(
        center_index=center,
        observed_index=observed,
        side=side,
        level=level,
        event_time_ns=center * 10 + 9 if event is None else event,
        observed_time_ns=observed * 10 + 9 if observed_time is None else observed_time,
    )


class CommonCoreBuilderTests(unittest.TestCase):
    def test_three_reactions_update_one_structure_instead_of_pair_votes(self) -> None:
        bars = [
            candle(0, 99.0, 102.0, 98.0, 100.0),   # HIGH interval [100, 102]
            candle(1, 100.0, 100.5, 95.0, 96.0),
            candle(2, 101.0, 103.0, 99.0, 100.5),  # HIGH interval [101, 103]
            candle(3, 100.0, 100.5, 96.0, 97.0),
            candle(4, 101.5, 102.5, 99.5, 101.0),  # HIGH interval [101.5, 102.5]
            candle(5, 101.0, 101.2, 98.0, 99.0),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=102.0),
            pivot(center=2, observed=3, side="HIGH", level=103.0),
            pivot(center=4, observed=5, side="HIGH", level=102.5),
        ]
        versions = build_common_core_structures(
            symbol="BTCUSDT",
            candles=bars,
            pivots=pivots,
            timeframe_minutes=15,
        )
        self.assertEqual(len(versions), 2)
        first, updated = versions
        self.assertEqual(first.version, 1)
        self.assertEqual(first.zone_low, 101.0)
        self.assertEqual(first.zone_high, 102.0)
        self.assertEqual(first.anchor_count, 2)
        self.assertEqual(updated.structure_id, first.structure_id)
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.supersedes_version_ids, (first.version_id,))
        self.assertEqual(updated.zone_low, 101.5)
        self.assertEqual(updated.zone_high, 102.0)
        self.assertEqual(updated.anchor_count, 3)

    def test_chain_overlap_without_common_core_starts_distinct_structure(self) -> None:
        bars = [
            candle(0, 99.0, 102.0, 98.0, 100.0),   # [100, 102]
            candle(1, 100.0, 100.5, 95.0, 96.0),
            candle(2, 101.0, 103.0, 99.0, 100.5),  # [101, 103]
            candle(3, 100.5, 101.0, 96.0, 97.0),
            candle(4, 102.5, 104.0, 100.0, 102.2), # [102.5, 104]
            candle(5, 102.0, 102.4, 98.0, 99.0),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=102.0),
            pivot(center=2, observed=3, side="HIGH", level=103.0),
            pivot(center=4, observed=5, side="HIGH", level=104.0),
        ]
        versions = build_common_core_structures(
            symbol="BTCUSDT",
            candles=bars,
            pivots=pivots,
            timeframe_minutes=15,
        )
        self.assertEqual(len(versions), 2)
        first, second = versions
        self.assertNotEqual(first.structure_id, second.structure_id)
        self.assertEqual(first.zone_low, 101.0)
        self.assertEqual(first.zone_high, 102.0)
        self.assertEqual(second.zone_low, 102.5)
        self.assertEqual(second.zone_high, 103.0)
        self.assertEqual(second.supersedes_version_ids, ())

    def test_body_acceptance_between_reactions_prevents_old_anchor_reuse(self) -> None:
        bars = [
            candle(0, 99.0, 102.0, 98.0, 100.0),
            candle(1, 100.0, 100.5, 95.0, 96.0),
            candle(2, 101.0, 103.0, 99.0, 100.5),
            candle(3, 100.5, 101.0, 97.0, 98.0),  # second pivot confirmed here
            candle(4, 101.0, 103.2, 100.0, 102.8),  # later acceptance through both candidate cores
            candle(5, 101.5, 102.5, 99.5, 101.0),
            candle(6, 101.0, 101.2, 98.0, 99.0),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=102.0),
            pivot(center=2, observed=3, side="HIGH", level=103.0),
            pivot(center=5, observed=6, side="HIGH", level=102.5),
        ]
        versions = build_common_core_structures(
            symbol="BTCUSDT",
            candles=bars,
            pivots=pivots,
            timeframe_minutes=15,
        )
        # The first structure was real and observable, but the close through it
        # ends that lifecycle.  The later pivot cannot update it or pair with the
        # broken prior reaction as though the defense had remained continuous.
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].version, 1)


class CommonCoreEngineTests(unittest.TestCase):
    @staticmethod
    def structure(
        *,
        version: int = 1,
        observed_time_ns: int = 39,
        supersedes: tuple[str, ...] = (),
        version_id: str | None = None,
    ) -> CommonCoreStructureVersion:
        first = pivot(
            center=0,
            observed=1,
            side="HIGH",
            level=101.0,
            event=9,
            observed_time=19,
        )
        second = pivot(
            center=2,
            observed=3,
            side="HIGH",
            level=101.2,
            event=29,
            observed_time=39,
        )
        anchors = [first, second]
        if version > 1:
            anchors.append(
                pivot(
                    center=4,
                    observed=7,
                    side="HIGH",
                    level=101.1,
                    event=49,
                    observed_time=observed_time_ns,
                )
            )
        return CommonCoreStructureVersion(
            structure_id="structure-root",
            version_id=version_id or f"structure-root-v{version}",
            version=version,
            supersedes_version_ids=supersedes,
            symbol="BTCUSDT",
            side=Side.LONG,
            observed_time_ns=observed_time_ns,
            timeframe_minutes=15,
            zone_low=100.0,
            zone_high=101.0,
            anchors=tuple(anchors),
        )

    @staticmethod
    def pivots():
        origin = pivot(
            center=3,
            observed=4,
            side="LOW",
            level=95.0,
            event=30,
            observed_time=45,
        )
        objective = pivot(
            center=1,
            observed=2,
            side="HIGH",
            level=112.0,
            event=15,
            observed_time=25,
        )
        return [objective, origin]

    def engine(self, structures):
        return CommonCoreAcceptedBreakEngine(
            "BTCUSDT",
            structures,
            self.pivots(),
            CommonCoreAcceptedBreakConfig(
                tick_size=0.1,
                signal_timeframe_minutes=5,
                valid_until_ns=1000,
            ),
        )

    def test_loose_fvg_is_a_census_observation_not_entry_geometry(self) -> None:
        engine = self.engine([self.structure()])
        loose = FootprintRef(
            footprint_id="loose-fvg",
            kind="FVG",
            side=Side.LONG,
            observed_time_ns=65,
            zone_low=102.0,
            zone_high=103.0,
            invalidation=99.0,
            source_two_x_quality=False,
            timeframe_minutes=5,
        )
        engine.on_close(candle(5, 100.5, 103.0, 100.0, 102.0), 0)
        engine.ingest_footprints([loose])
        update = engine.on_close(candle(6, 101.5, 104.0, 101.2, 103.5), 1)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertEqual(setup.entry, 101.0)
        self.assertIn("COMMON_CORE_STRUCTURE", setup.family)
        self.assertEqual(
            engine.diagnostics["loose_fvg_rejected_source_definition"],
            1,
        )

    def test_source_valid_fvg_can_supply_first_reachable_entry(self) -> None:
        engine = self.engine([self.structure()])
        strict = FootprintRef(
            footprint_id="strict-fvg",
            kind="FVG",
            side=Side.LONG,
            observed_time_ns=65,
            zone_low=102.0,
            zone_high=103.0,
            invalidation=99.0,
            source_two_x_quality=True,
            timeframe_minutes=5,
        )
        engine.on_close(candle(5, 100.5, 103.0, 100.0, 102.0), 0)
        engine.ingest_footprints([strict])
        update = engine.on_close(candle(6, 103.2, 104.0, 103.1, 103.5), 1)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertEqual(setup.entry, 103.0)
        self.assertIn("STRICT_FVG", setup.family)
        self.assertEqual(setup.body_ratio, 2.0)

    def test_new_structure_version_cancels_superseded_pending_intent(self) -> None:
        first = self.structure(version=1, observed_time_ns=39, version_id="v1")
        second = self.structure(
            version=2,
            observed_time_ns=79,
            supersedes=("v1",),
            version_id="v2",
        )
        engine = self.engine([first, second])
        engine.on_close(candle(5, 100.5, 103.0, 100.0, 102.0), 0)
        accepted = engine.on_close(candle(6, 101.5, 104.0, 101.2, 103.0), 1)
        self.assertEqual(len(accepted.setups), 1)
        pending_id = accepted.setups[0].setup_id
        update = engine.on_close(candle(8, 100.5, 101.0, 99.0, 100.0), 2)
        self.assertEqual(update.cancel_setup_ids, (pending_id,))
        self.assertIn("v2", engine.active)
        self.assertNotIn("v1", engine.active)


if __name__ == "__main__":
    unittest.main()
