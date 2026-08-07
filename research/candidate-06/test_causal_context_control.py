from __future__ import annotations

from types import SimpleNamespace
import unittest

from causal_context_control import first_matching_reason, signal_exit_contract


class CausalContextControlTests(unittest.TestCase):
    def test_first_matching_reason_uses_only_declared_codes(self):
        transitions = [
            SimpleNamespace(reason_code="UNRELATED"),
            SimpleNamespace(reason_code="BOUNDARY_LOST"),
            SimpleNamespace(reason_code="ORIGIN_REBALANCED"),
        ]
        self.assertEqual(
            first_matching_reason(transitions, ["ORIGIN_REBALANCED", "BOUNDARY_LOST"]),
            "BOUNDARY_LOST",
        )
        self.assertIsNone(first_matching_reason(transitions, ["NOT_PRESENT"]))

    def test_signal_contract_is_explicit_and_defaults_to_off(self):
        signal = SimpleNamespace(
            details={
                "causal_exit_reason_codes": ["A", "B"],
                "causal_exit_open_position": True,
            },
        )
        self.assertEqual(signal_exit_contract(signal), (("A", "B"), True))
        self.assertEqual(signal_exit_contract(SimpleNamespace(details={})), ((), False))


if __name__ == "__main__":
    unittest.main()
