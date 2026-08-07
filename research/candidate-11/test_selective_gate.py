import unittest
from selective_gate import CalibrationKey, OnlineLogGrowthGate, TerminalOutcome


class SelectiveGateTests(unittest.TestCase):
    def key(self) -> CalibrationKey:
        return CalibrationKey("FAR", "LONDON_TO_NEW_YORK", "NORMAL")

    def test_abstains_without_terminal_calibration(self) -> None:
        gate = OnlineLogGrowthGate(min_bucket_samples=5, min_scenario_samples=8)
        decision = gate.decide(self.key(), net_r=2.0)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "INSUFFICIENT_CAUSAL_CALIBRATION")

    def test_approves_fixed_risk_when_lower_bound_is_positive(self) -> None:
        gate = OnlineLogGrowthGate(min_bucket_samples=8, min_scenario_samples=12, probability_safety_margin=0.0)
        for i in range(20):
            gate.observe(TerminalOutcome(i, self.key(), i < 19, 2.0))
        decision = gate.decide(self.key(), net_r=2.0)
        self.assertTrue(decision.approved)
        self.assertGreater(decision.win_probability_lower_bound, decision.log_growth_break_even_probability)

    def test_recent_error_rate_change_pauses_new_entries(self) -> None:
        gate = OnlineLogGrowthGate(min_bucket_samples=8, min_scenario_samples=12, drift_window=8, probability_safety_margin=0.0)
        for sequence, won in enumerate([True] * 8 + [False] * 8):
            gate.observe(TerminalOutcome(sequence, self.key(), won, 2.0))
        decision = gate.decide(self.key(), net_r=2.0)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "ONLINE_ERROR_RATE_CHANGE")

    def test_rejects_reordered_outcome_updates(self) -> None:
        gate = OnlineLogGrowthGate(min_bucket_samples=5, min_scenario_samples=8)
        gate.observe(TerminalOutcome(1, self.key(), True, 2.0))
        with self.assertRaisesRegex(ValueError, "strict sequence"):
            gate.observe(TerminalOutcome(1, self.key(), True, 2.0))


if __name__ == "__main__":
    unittest.main()
