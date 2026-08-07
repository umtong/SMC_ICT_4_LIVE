from __future__ import annotations

from types import SimpleNamespace
import unittest

from nautilus_execution import NautilusExecutionMixin


class _Portfolio:
    def __init__(self, flat: bool) -> None:
        self.flat = flat

    def is_flat(self, _instrument_id) -> bool:
        return self.flat


class _Harness(NautilusExecutionMixin):
    def __init__(self, *, flat: bool = False) -> None:
        self.config = SimpleNamespace(instrument_id="BTCUSDT-PERP.BINANCE")
        self.portfolio = _Portfolio(flat)
        self.errors: list[str] = []
        self.diagnostics: dict[str, object] = {}
        self._active_trade = None
        self._entry_inflight = False
        self._exit_inflight = False
        self._scenario_states: dict[str, str] = {}
        self.cancel_calls = 0
        self.close_calls = 0
        self.transitions: list[dict[str, object]] = []

    def cancel_all_orders(self, _instrument_id) -> None:
        self.cancel_calls += 1

    def close_all_positions(self, _instrument_id) -> None:
        self.close_calls += 1

    def _record_external_transition(self, **kwargs) -> None:
        self.transitions.append(kwargs)

    def position(self, *, partial: bool = False) -> None:
        self._active_trade = {
            "scenario_id": "ADOM-1",
            "entry_execution_mode": "DEFENSE_ORIGIN_LIMIT",
            "partial_entry_abort_requested": partial,
        }
        self._scenario_states = {"ADOM-1": "POSITION"}


class AdomRejectionHardeningTests(unittest.TestCase):
    def event(self, reason: str):
        return SimpleNamespace(reason=reason, ts_event=123)

    def test_partial_abort_stop_collision_does_not_duplicate_flatten(self):
        harness = _Harness(flat=False)
        harness.position(partial=True)
        harness._exit_inflight = True
        harness._handle_order_failure(
            self.event("STOP_MARKET SELL order stop px of 100 was in the market: bid=99, ask=101"),
            "ORDER_REJECTED",
        )
        self.assertEqual(harness.errors, [])
        self.assertEqual(harness.cancel_calls, 0)
        self.assertEqual(harness.close_calls, 0)
        self.assertEqual(
            harness._active_trade["forced_exit_reason"],
            "PASSIVE_FILL_STOP_ALREADY_CROSSED",
        )

    def test_full_passive_fill_stop_collision_flattens_once(self):
        harness = _Harness(flat=False)
        harness.position(partial=False)
        harness._handle_order_failure(
            self.event("STOP_MARKET BUY order stop px of 100 was in the market: bid=99, ask=101"),
            "ORDER_REJECTED",
        )
        self.assertEqual(harness.errors, [])
        self.assertTrue(harness._exit_inflight)
        self.assertEqual(harness.cancel_calls, 1)
        self.assertEqual(harness.close_calls, 1)

    def test_late_contingent_rejection_during_abort_is_diagnostic(self):
        harness = _Harness(flat=False)
        harness.position(partial=True)
        harness._exit_inflight = True
        harness._handle_order_failure(
            self.event("Contingent order O-1 already closed"),
            "ORDER_REJECTED",
        )
        self.assertEqual(harness.errors, [])
        self.assertEqual(harness.close_calls, 0)

    def test_stale_reduce_only_after_flat_is_diagnostic(self):
        harness = _Harness(flat=True)
        harness._handle_order_failure(
            self.event("REDUCE_ONLY MARKET SELL order would have increased position"),
            "ORDER_REJECTED",
        )
        self.assertEqual(harness.errors, [])
        self.assertFalse(harness._exit_inflight)

    def test_unrelated_protection_failure_remains_error(self):
        harness = _Harness(flat=False)
        harness.position(partial=False)
        harness._handle_order_failure(
            self.event("venue rejected protective order for unknown reason"),
            "ORDER_REJECTED",
        )
        self.assertEqual(len(harness.errors), 1)
        self.assertEqual(harness.cancel_calls, 1)
        self.assertEqual(harness.close_calls, 1)
        self.assertEqual(harness._active_trade["forced_exit_reason"], "PROTECTION_FAILURE")


if __name__ == "__main__":
    unittest.main()
