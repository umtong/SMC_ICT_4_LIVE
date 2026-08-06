from __future__ import annotations

import unittest

from entry_confirmation import DefenseCheck, continuation_defense_passes


class ContinuationDefenseTests(unittest.TestCase):
    def check(self, mode: str, *, direction: str = "LONG", boundary: float = 100.0,
              reference: float = 102.0, open_: float = 101.0, close: float = 103.0,
              flow: float = 0.2) -> bool:
        return continuation_defense_passes(
            DefenseCheck(mode, direction, boundary, reference, open_, close, flow),
        )

    def test_boundary_must_remain_held(self) -> None:
        self.assertFalse(self.check("DIRECTIONAL_BODY", close=99.9))
        self.assertFalse(self.check("DIRECTIONAL_FLOW", close=99.9))

    def test_directional_body_is_independent_of_reference_hold(self) -> None:
        self.assertTrue(self.check("DIRECTIONAL_BODY", open_=100.5, close=101.5))
        self.assertFalse(self.check("REFERENCE_HOLD", open_=100.5, close=101.5))

    def test_directional_flow_rejects_opposing_pressure(self) -> None:
        self.assertTrue(self.check("DIRECTIONAL_FLOW", flow=0.01))
        self.assertFalse(self.check("DIRECTIONAL_FLOW", flow=-0.01))

    def test_reference_and_flow_requires_both(self) -> None:
        self.assertTrue(self.check("REFERENCE_HOLD_AND_FLOW", close=102.1, flow=0.1))
        self.assertFalse(self.check("REFERENCE_HOLD_AND_FLOW", close=102.1, flow=-0.1))
        self.assertFalse(self.check("REFERENCE_HOLD_AND_FLOW", close=101.9, flow=0.1))

    def test_short_is_symmetric(self) -> None:
        self.assertTrue(self.check(
            "BODY_AND_FLOW", direction="SHORT", boundary=100.0, reference=98.0,
            open_=99.0, close=97.0, flow=-0.2,
        ))
        self.assertFalse(self.check(
            "BODY_AND_FLOW", direction="SHORT", boundary=100.0, reference=98.0,
            open_=97.0, close=99.0, flow=-0.2,
        ))


if __name__ == "__main__":
    unittest.main()
