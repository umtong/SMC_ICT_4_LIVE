from __future__ import annotations

from collections import deque
import unittest

from analyze_phase_memory import MIN_PHASE_OBSERVATIONS
from analyze_phase_memory import MIN_ROUTE_OBSERVATIONS
from analyze_phase_memory import Outcome
from analyze_phase_memory import phase_gate


class Candidate31PhaseMemoryContractTest(unittest.TestCase):
    def _outcomes(self, n: int, value: float):
        return deque(
            Outcome(observed_ns=index + 1, value=value)
            for index in range(n)
        )

    def test_positive_matured_phase_and_route_pass(self) -> None:
        decision, detail = phase_gate(
            self._outcomes(MIN_PHASE_OBSERVATIONS, 0.004),
            self._outcomes(MIN_ROUTE_OBSERVATIONS, 0.003),
        )
        self.assertTrue(decision)
        self.assertGreater(detail["shrunk_mean"], 0.0)

    def test_negative_route_blocks_positive_local_phase(self) -> None:
        decision, detail = phase_gate(
            self._outcomes(MIN_PHASE_OBSERVATIONS + 4, 0.004),
            self._outcomes(MIN_ROUTE_OBSERVATIONS, -0.003),
        )
        self.assertFalse(decision)
        self.assertLess(detail["route_mean"], 0.0)

    def test_insufficient_matured_history_never_passes(self) -> None:
        decision, detail = phase_gate(
            self._outcomes(MIN_PHASE_OBSERVATIONS - 1, 0.100),
            self._outcomes(MIN_ROUTE_OBSERVATIONS, 0.100),
        )
        self.assertFalse(decision)
        self.assertEqual(detail["phase_n"], MIN_PHASE_OBSERVATIONS - 1)


if __name__ == "__main__":
    unittest.main()
