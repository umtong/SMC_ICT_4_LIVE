from __future__ import annotations

from types import SimpleNamespace
import os
import unittest

from logic import Direction, Pool, Scenario, Side, TradePlan

from c10_v52_overlay import (
    external_runner_enabled,
    funded_equilibrium_runner_enabled,
    reframe_external_runner,
)


def pool(
    scenario_id: str,
    side: Side,
    level: float,
    *,
    opposite: float | None = None,
    strength: int = 1,
    confirmed: int = 10,
) -> Pool:
    return Pool(
        scenario_id=scenario_id,
        side=side,
        level=level,
        source="COMPLETED_4H_AUCTION",
        candidate_ts_ns=confirmed - 1,
        confirmed_ts_ns=confirmed,
        confirmed_index=1,
        expiry_index=1000,
        opposite_level=opposite,
        strength=strength,
        external=True,
    )


def plan() -> TradePlan:
    return TradePlan(
        scenario_id="SOURCE",
        scenario=Scenario.FAR,
        direction=Direction.LONG,
        observed_ts_ns=100,
        expected_entry=95.0,
        stop_price=89.0,
        target_price=100.0,
        atr=2.0,
        loss_per_unit=6.2,
        gain_per_unit=4.9,
        net_r=0.79,
        reason_code="SOURCE_EQUILIBRIUM_PRIMARY",
        expire_ts_ns=1000,
        details={"confirmation_close": 96.0},
    )


def logic(*pools: Pool):
    config = SimpleNamespace(
        draw_dominance_min=0.15,
        effective_maker_rate=0.0004,
        min_net_r=1.25,
    )
    value = SimpleNamespace(pools=list(pools), config=config, _index=100)
    value._liquidity_hazard = lambda item, price, atr: (
        float(item.strength) / max(abs(float(item.level) - price) / atr, 0.20)
    )
    return value


class ExternalRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_runner = os.environ.get("C10_V52_EXTERNAL_RUNNER")
        self.old_funded = os.environ.get("C10_V52_FUNDED_EQUILIBRIUM")
        os.environ["C10_V52_EXTERNAL_RUNNER"] = "1"
        os.environ["C10_V52_FUNDED_EQUILIBRIUM"] = "1"

    def tearDown(self) -> None:
        for name, value in (
            ("C10_V52_EXTERNAL_RUNNER", self.old_runner),
            ("C10_V52_FUNDED_EQUILIBRIUM", self.old_funded),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_flags_are_independent_exact_axes(self) -> None:
        self.assertTrue(external_runner_enabled())
        self.assertTrue(funded_equilibrium_runner_enabled())
        os.environ["C10_V52_FUNDED_EQUILIBRIUM"] = "0"
        self.assertTrue(external_runner_enabled())
        self.assertFalse(funded_equilibrium_runner_enabled())

    def test_agreeing_preexisting_external_hazard_reframes_target(self) -> None:
        source = pool("SOURCE", Side.LOW, 90.0, opposite=110.0)
        high = pool("HIGH", Side.HIGH, 120.0, strength=4)
        low = pool("LOW", Side.LOW, 80.0, strength=1)
        decision = reframe_external_runner(plan(), logic(source, high, low))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.plan.target_price, 120.0)
        self.assertEqual(
            decision.plan.details["source_equilibrium_checkpoint"],
            100.0,
        )
        self.assertEqual(
            decision.plan.details["external_runner"]["external_target_pool_id"],
            "HIGH",
        )

    def test_target_must_be_known_strictly_before_plan(self) -> None:
        source = pool("SOURCE", Side.LOW, 90.0, opposite=110.0)
        high = pool("HIGH", Side.HIGH, 120.0, strength=10, confirmed=100)
        decision = reframe_external_runner(plan(), logic(source, high))
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "NO_AGREEING_INDEPENDENT_EXTERNAL_RUNNER_DRAW",
        )

    def test_counter_hazard_dominance_rejects_runner(self) -> None:
        source = pool("SOURCE", Side.LOW, 90.0, opposite=110.0)
        high = pool("HIGH", Side.HIGH, 120.0, strength=1)
        low = pool("LOW", Side.LOW, 80.0, strength=5)
        decision = reframe_external_runner(plan(), logic(source, high, low))
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "NO_AGREEING_INDEPENDENT_EXTERNAL_RUNNER_DRAW",
        )

    def test_disabled_runner_preserves_plan_identity(self) -> None:
        os.environ["C10_V52_EXTERNAL_RUNNER"] = "0"
        original = plan()
        decision = reframe_external_runner(original, logic())
        self.assertTrue(decision.approved)
        self.assertIs(decision.plan, original)


if __name__ == "__main__":
    unittest.main()
