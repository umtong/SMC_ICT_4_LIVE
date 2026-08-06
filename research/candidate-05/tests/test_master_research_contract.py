from __future__ import annotations

from dataclasses import asdict
import unittest

from master_research import RangeSpec
from master_research import btc_long_gate
from master_research import classify_week1
from master_research import integrity_checks


class MasterResearchContractTest(unittest.TestCase):
    def valid_run(self, **overrides):
        base = {
            "available": True,
            "integrity_checks": {
                "engine_is_nautilus": True,
                "positive_nav": True,
                "no_liquidation": True,
                "no_order_rejections": True,
                "no_order_denials": True,
                "single_entry_intent": True,
                "single_position": True,
                "nautilus_positions_consistent": True,
                "nautilus_orders_consistent": True,
            },
            "geometric_daily_growth": 0.005,
            "total_return": 0.03,
            "calendar_days": 7,
            "trades": 5,
            "wins": 3,
            "losses": 2,
            "active_days": 4,
            "largest_winner_share": 0.2,
            "max_drawdown": 0.05,
            "scenario_metrics": {},
        }
        base.update(overrides)
        return base

    def test_zero_trade_completed_run_is_not_an_implementation_error(self) -> None:
        checks = integrity_checks(
            {
                "engine": "NautilusTrader BacktestNode",
                "ending_nav": 100000.0,
                "liquidations": 0,
                "trades": 0,
                "gate_checks": {
                    "nautilus_orders": False,
                    "nautilus_positions": True,
                },
                "strategy_diagnostics": {
                    "order_rejections": 0,
                    "order_denials": 0,
                    "max_simultaneous_entry_intents": 0,
                    "max_open_positions_observed": 0,
                },
            },
        )
        self.assertTrue(all(checks.values()))

    def test_no_incremental_trade_is_classified_as_logic_failure(self) -> None:
        baseline = self.valid_run()
        candidate = self.valid_run(
            geometric_daily_growth=0.006,
            scenario_metrics={
                "NEW_BRANCH": {"trades": 0, "wins": 0, "net_pnl": 0.0},
            },
        )
        decision = classify_week1(
            baseline=baseline,
            candidate=candidate,
            branch="NEW_BRANCH",
        )
        self.assertEqual(
            decision["classification"],
            "LOGIC_FAILURE_NO_INCREMENTAL_EXECUTABLE_OPPORTUNITY",
        )
        self.assertFalse(decision["passed"])

    def test_positive_incremental_branch_must_improve_control(self) -> None:
        baseline = self.valid_run(geometric_daily_growth=0.005)
        candidate = self.valid_run(
            geometric_daily_growth=0.006,
            scenario_metrics={
                "NEW_BRANCH": {"trades": 3, "wins": 2, "net_pnl": 1200.0},
            },
        )
        decision = classify_week1(
            baseline=baseline,
            candidate=candidate,
            branch="NEW_BRANCH",
        )
        self.assertEqual(decision["classification"], "LOGIC_SCREEN_PASSED_WEEK_1")
        self.assertTrue(decision["passed"])

        candidate["geometric_daily_growth"] = 0.004
        decision = classify_week1(
            baseline=baseline,
            candidate=candidate,
            branch="NEW_BRANCH",
        )
        self.assertEqual(decision["classification"], "LOGIC_FAILURE_DID_NOT_IMPROVE_CONTROL")

    def test_btc_long_gate_requires_growth_density_and_win_dispersion(self) -> None:
        run = self.valid_run(
            geometric_daily_growth=0.011,
            trades=50,
            active_days=35,
            largest_winner_share=0.20,
            max_drawdown=0.25,
        )
        gate = btc_long_gate(run)
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["classification"], "BTC_91D_ALPHA_GATE_PASSED")

        run["largest_winner_share"] = 0.40
        gate = btc_long_gate(run)
        self.assertFalse(gate["passed"])
        self.assertFalse(gate["checks"]["largest_winner_share"])

    def test_slot_dataclass_serializes_with_asdict(self) -> None:
        spec = RangeSpec("test", "2024-01-01", "2024-01-02", "2024-01-01", "2024-01-02", 2)
        self.assertEqual(asdict(spec)["calendar_days"], 2)
        self.assertFalse(hasattr(spec, "__dict__"))


if __name__ == "__main__":
    unittest.main()
