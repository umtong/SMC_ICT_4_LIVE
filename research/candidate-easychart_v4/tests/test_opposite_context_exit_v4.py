from __future__ import annotations

from types import SimpleNamespace
import unittest

from domain import Side
from mtf_strategy_v4_context_exit import OppositeContextExitMixin


class _Portfolio:
    def __init__(self, flat: bool = False) -> None:
        self.flat = flat

    def is_flat(self, _instrument_id) -> bool:
        return self.flat


class _BaseHarness:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []
        self.canceled: list[str] = []
        self.closed: list[str] = []
        self.portfolio = _Portfolio(False)
        self.active_instrument_id = "BTCUSDT-PERP.BINANCE"
        self.active_plan = SimpleNamespace(
            plan_id="PLAN-1",
            side=Side.LONG,
        )

    def _record(self, kind: str, **values) -> None:
        self.records.append((kind, values))

    def cancel_all_orders(self, instrument_id) -> None:
        self.canceled.append(str(instrument_id))

    def close_all_positions(self, instrument_id) -> None:
        self.closed.append(str(instrument_id))

    def _submit_plan(self, _instrument_id, _plan) -> bool:
        return True


class _Harness(OppositeContextExitMixin, _BaseHarness):
    pass


class OppositeContextExitTests(unittest.TestCase):
    @staticmethod
    def transition(
        *,
        scenario_kind: str,
        context_side: str,
        instrument_id: str = "BTCUSDT-PERP.BINANCE",
        scale_name: str = "MACRO",
    ) -> dict:
        return {
            "scenario_kind": scenario_kind,
            "context_side": context_side,
            "instrument_id": instrument_id,
            "scale_name": scale_name,
            "context_path": "FAKEOUT",
            "context_structure_kind": "CHANNEL_UPPER",
            "event_id": "BTCUSDT:60m:STRUCTURE_EVENT:00000123",
            "event_time_ns": 123,
        }

    def test_confirmed_opposite_macro_context_requests_native_exit(self) -> None:
        harness = _Harness()
        harness._record(
            "scenario_transition",
            **self.transition(
                scenario_kind="context_confirmed_fakeout_activated",
                context_side="SHORT",
            ),
        )
        self.assertTrue(harness.context_exit_requested)
        self.assertEqual(harness.canceled, ["BTCUSDT-PERP.BINANCE"])
        self.assertEqual(harness.closed, ["BTCUSDT-PERP.BINANCE"])
        kinds = [kind for kind, _ in harness.records]
        self.assertIn("opposite_context_exit_requested", kinds)

    def test_same_side_confirmation_does_not_exit(self) -> None:
        harness = _Harness()
        harness._record(
            "scenario_transition",
            **self.transition(
                scenario_kind="context_acceptance_first_retest_confirmed",
                context_side="LONG",
            ),
        )
        self.assertFalse(harness.context_exit_requested)
        self.assertEqual(harness.closed, [])

    def test_pending_acceptance_is_not_an_exit_signal(self) -> None:
        harness = _Harness()
        harness._record(
            "scenario_transition",
            **self.transition(
                scenario_kind="context_acceptance_waiting_first_retest",
                context_side="SHORT",
            ),
        )
        self.assertFalse(harness.context_exit_requested)
        self.assertEqual(harness.closed, [])

    def test_opposite_event_on_other_symbol_does_not_exit_global_trade(self) -> None:
        harness = _Harness()
        harness._record(
            "scenario_transition",
            **self.transition(
                scenario_kind="context_structural_event_activated",
                context_side="SHORT",
                instrument_id="ETHUSDT-PERP.BINANCE",
            ),
        )
        self.assertFalse(harness.context_exit_requested)
        self.assertEqual(harness.closed, [])

    def test_micro_context_transition_cannot_replace_1h_premise(self) -> None:
        harness = _Harness()
        harness._record(
            "scenario_transition",
            **self.transition(
                scenario_kind="context_structural_event_activated",
                context_side="SHORT",
                scale_name="MICRO",
            ),
        )
        self.assertFalse(harness.context_exit_requested)
        self.assertEqual(harness.closed, [])

    def test_flat_pending_entry_is_canceled_but_not_closed(self) -> None:
        harness = _Harness()
        harness.portfolio.flat = True
        harness._record(
            "scenario_transition",
            **self.transition(
                scenario_kind="context_structural_event_activated",
                context_side="SHORT",
            ),
        )
        self.assertEqual(harness.canceled, ["BTCUSDT-PERP.BINANCE"])
        self.assertEqual(harness.closed, [])


if __name__ == "__main__":
    unittest.main()
