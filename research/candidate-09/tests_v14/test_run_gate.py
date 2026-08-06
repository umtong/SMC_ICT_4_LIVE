from __future__ import annotations

import unittest

from run import DetailedRun, RunOutcome, evaluate_gate, pooled_metrics


def outcome(*, segment: str, total_return: float, trades: int, largest_share: float | None) -> RunOutcome:
    return RunOutcome(
        run_id=f"baseline:{segment}",
        variant="baseline",
        segment=segment,
        start_ns=0,
        end_ns=7 * 86_400_000_000_000,
        calendar_days=7.0,
        starting_nav=100000.0,
        ending_nav=100000.0 * (1.0 + total_return),
        total_return=total_return,
        daily_geometric_return=(1.0 + total_return) ** (1.0 / 7.0) - 1.0,
        trades=trades,
        wins=trades,
        losses=0,
        win_rate=1.0,
        profit_factor=float("inf"),
        expectancy_r=1.0,
        max_drawdown=0.05 if total_return < 0 else 0.0,
        maximum_consecutive_losses=1 if total_return < 0 else 0,
        largest_profit_share=largest_share,
        reversal_trades=trades,
        continuation_trades=0,
        rejected_orders=0,
        time_exits=0,
        missing_feature_bars=0,
        open_position_at_stop=False,
        native_account_final=100000.0 * (1.0 + total_return),
        native_expected_final=100000.0 * (1.0 + total_return),
        accounting_error=0.0,
        implementation_status="OK",
    )


def detail(result: RunOutcome, pnls: list[float]) -> DetailedRun:
    return DetailedRun(
        outcome=result,
        events=[],
        trades=[{"net_pnl": pnl, "opened_ns": index + 1} for index, pnl in enumerate(pnls)],
        fills=[],
    )


class PooledGateContractTest(unittest.TestCase):
    def test_negative_week_is_allowed_when_predeclared_period_passes(self):
        details = [
            detail(outcome(segment="a", total_return=0.20, trades=5, largest_share=1.0), [100.0] * 5),
            detail(outcome(segment="b", total_return=-0.05, trades=5, largest_share=None), [-20.0] * 5),
            detail(outcome(segment="c", total_return=0.10, trades=5, largest_share=1.0), [100.0] * 5),
        ]
        config = {
            "gate": {
                "minimum_pooled_daily_geometric_return": 0.01,
                "minimum_total_trades": 15,
                "minimum_active_weeks": 3,
                "maximum_single_trade_profit_share": 0.35,
            },
        }
        passed, report = evaluate_gate(config, details)
        self.assertTrue(passed)
        self.assertNotIn("all_weeks_positive", report["checks"])
        self.assertGreater(report["pooled"]["daily_geometric_return"], 0.01)

    def test_profit_concentration_is_computed_across_the_whole_period(self):
        outcomes = [
            outcome(segment="a", total_return=0.10, trades=1, largest_share=1.0),
            outcome(segment="b", total_return=0.10, trades=2, largest_share=0.5),
            outcome(segment="c", total_return=0.10, trades=2, largest_share=0.5),
        ]
        trades = [
            {"net_pnl": 100.0},
            {"net_pnl": 100.0},
            {"net_pnl": 100.0},
            {"net_pnl": 100.0},
            {"net_pnl": 100.0},
        ]
        pooled = pooled_metrics(outcomes, trades)
        self.assertAlmostEqual(pooled["maximum_single_trade_profit_share"], 0.20)
        self.assertLess(pooled["maximum_single_trade_profit_share"], max(o.largest_profit_share or 0.0 for o in outcomes))


if __name__ == "__main__":
    unittest.main()
