from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest

import pandas as pd

from domain import Side
import mtf_backtest_support_context_exit as context_audit
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
        self.active_entry_id = "ENTRY-1"
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

    def on_order_filled(self, event) -> None:
        self.records.append(("base_order_filled", {"client_order_id": str(event.client_order_id)}))


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

    @staticmethod
    def fill(*, client_order_id: str, order_type: str) -> SimpleNamespace:
        return SimpleNamespace(
            client_order_id=client_order_id,
            venue_order_id="VENUE-1",
            position_id="POSITION-1",
            instrument_id="BTCUSDT-PERP.BINANCE",
            order_type=SimpleNamespace(name=order_type),
            last_qty="1",
            last_px="100",
            commission="1 USDT",
            ts_event=456,
        )

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

    def test_native_market_close_records_exact_context_exit_order_id(self) -> None:
        harness = _Harness()
        harness._record(
            "scenario_transition",
            **self.transition(
                scenario_kind="context_structural_event_activated",
                context_side="SHORT",
            ),
        )
        harness.on_order_filled(
            self.fill(client_order_id="CONTEXT-CLOSE-1", order_type="MARKET"),
        )
        context_fills = [
            values
            for kind, values in harness.records
            if kind == "context_exit_order_filled"
        ]
        self.assertEqual(len(context_fills), 1)
        self.assertEqual(context_fills[0]["client_order_id"], "CONTEXT-CLOSE-1")

    def test_protective_nonmarket_fill_is_not_relabelled_context_exit(self) -> None:
        harness = _Harness()
        harness._record(
            "scenario_transition",
            **self.transition(
                scenario_kind="context_structural_event_activated",
                context_side="SHORT",
            ),
        )
        harness.on_order_filled(
            self.fill(client_order_id="STOP-1", order_type="STOP_MARKET"),
        )
        self.assertFalse(
            any(kind == "context_exit_order_filled" for kind, _ in harness.records),
        )

    def test_exact_client_id_reconciles_only_matching_untagged_exit(self) -> None:
        strategy = SimpleNamespace(
            event_log=[
                {
                    "kind": "context_exit_order_filled",
                    "client_order_id": "CONTEXT-CLOSE-1",
                },
            ],
        )
        original = context_audit._ORIGINAL_BUILD_AUDIT
        context_audit._ORIGINAL_BUILD_AUDIT = lambda *args, **kwargs: pd.DataFrame(
            [
                {"closing_order_id": "CONTEXT-CLOSE-1", "exit_role": None},
                {"closing_order_id": "UNRELATED-1", "exit_role": None},
                {"closing_order_id": "STOP-1", "exit_role": "STOP_LOSS"},
            ],
        )
        try:
            audit = context_audit._build_context_exit_trade_audit(
                strategy,
                pd.DataFrame(),
                pd.DataFrame(),
                date(2024, 1, 1),
            )
        finally:
            context_audit._ORIGINAL_BUILD_AUDIT = original
        self.assertEqual(audit.loc[0, "exit_role"], "CONTEXT_EXIT")
        self.assertTrue(pd.isna(audit.loc[1, "exit_role"]))
        self.assertEqual(audit.loc[2, "exit_role"], "STOP_LOSS")

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
