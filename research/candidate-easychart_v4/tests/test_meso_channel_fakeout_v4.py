from __future__ import annotations

from types import SimpleNamespace
import unittest

from domain import Side
from market_structure import StructureKind, StructurePath
from scenario_runtime_v4_meso_channel_fakeout import (
    ChannelFakeoutMesoResearchBundle,
)


class MesoChannelFakeoutTests(unittest.TestCase):
    @staticmethod
    def plan(*, path: StructurePath, kind: StructureKind) -> SimpleNamespace:
        return SimpleNamespace(
            plan_id=f"PLAN:{path.value}:{kind.value}",
            side=Side.LONG,
            scale_name="MESO",
            observed_time_ns=200,
            interaction_time_ns=100,
            higher_timeframe_minutes=5,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
            scenario_path=path.value,
            higher_zone_kind=kind,
        )

    def test_channel_fakeout_is_the_only_meso_family_eligible(self) -> None:
        bundle = ChannelFakeoutMesoResearchBundle("TEST", 0.1)
        self.assertTrue(
            bundle._is_channel_fakeout(
                self.plan(
                    path=StructurePath.FAKEOUT,
                    kind=StructureKind.CHANNEL_LOWER,
                ),
            ),
        )
        self.assertTrue(
            bundle._is_channel_fakeout(
                self.plan(
                    path=StructurePath.FAKEOUT,
                    kind=StructureKind.CHANNEL_UPPER,
                ),
            ),
        )
        for path, kind in (
            (StructurePath.BOUNCE, StructureKind.CHANNEL_LOWER),
            (StructurePath.TRAP_REENTRY, StructureKind.CHANNEL_UPPER),
            (StructurePath.ACCEPTANCE, StructureKind.CHANNEL_UPPER),
            (StructurePath.FAKEOUT, StructureKind.SWING_LOW),
            (StructurePath.FAKEOUT, StructureKind.TRENDLINE_SUPPORT),
        ):
            self.assertFalse(
                bundle._is_channel_fakeout(self.plan(path=path, kind=kind)),
            )

    def test_nonfamily_plan_is_logged_not_silently_scored(self) -> None:
        bundle = ChannelFakeoutMesoResearchBundle("TEST", 0.1)
        output = bundle._route_meso_plans(
            [
                self.plan(
                    path=StructurePath.TRAP_REENTRY,
                    kind=StructureKind.CHANNEL_LOWER,
                ),
            ],
        )
        self.assertEqual(output, [])
        self.assertEqual(
            bundle.diagnostics["top_down_router"].get(
                "meso_non_channel_fakeout_family_not_routed",
            ),
            1,
        )
        trace = bundle.drain_trace()
        self.assertTrue(
            any(
                event.get("scenario_kind")
                == "meso_non_channel_fakeout_family_not_routed"
                for event in trace
            ),
        )


if __name__ == "__main__":
    unittest.main()
