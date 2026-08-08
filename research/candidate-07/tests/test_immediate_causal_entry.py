from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from strategy_immediate_causal_entry import ImmediateCausalEntryMixin  # noqa: E402


class _Portfolio:
    def __init__(self, flat: bool = True) -> None:
        self.flat = flat

    def is_flat(self, instrument_id) -> bool:
        del instrument_id
        return self.flat


class _BaseHarness:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            trade_start_ns=100,
            trade_end_ns=1_000,
            instrument_id="BTCUSDT.BINANCE",
        )
        self.portfolio = _Portfolio(True)
        self._pending_plan = None
        self._active_plan = None
        self._exit_pending = False
        self.submitted = []
        self.invalidated = []
        self.events = []
        self.plan_to_create = None

    def on_bar(self, bar) -> None:
        del bar
        if self.plan_to_create is not None:
            self._pending_plan = self.plan_to_create
            self.plan_to_create = None

    def _submit_pending(self, bar) -> None:
        self.submitted.append((self._pending_plan.scenario_id, int(bar.ts_event)))
        self._active_plan = self._pending_plan
        self._pending_plan = None

    def _invalidate_pending(self, reason: str, event_time_ns: int) -> None:
        self.invalidated.append((reason, event_time_ns))
        self._pending_plan = None

    def _append_manual_event(self, **kwargs) -> None:
        self.events.append(kwargs)


class _Harness(ImmediateCausalEntryMixin, _BaseHarness):
    pass


def _plan(observed: int):
    return SimpleNamespace(
        scenario_id=f"scenario-{observed}",
        observed_time_ns=observed,
        entry_reference=100.0,
        stop_price=99.0,
        target_price=102.0,
        expected_rr=2.0,
        kind=SimpleNamespace(value="ABSORPTION_RECLAIM"),
    )


class ImmediateCausalEntryTests(unittest.TestCase):
    def test_new_plan_is_submitted_in_observation_callback(self) -> None:
        harness = _Harness()
        harness.plan_to_create = _plan(500)
        harness.on_bar(SimpleNamespace(ts_event=500))
        self.assertEqual(harness.submitted, [("scenario-500", 500)])
        self.assertEqual(
            harness.events[0]["reason_code"],
            "IMMEDIATE_POST_OBSERVATION_SUBMISSION",
        )

    def test_older_pending_plan_is_not_resubmitted_by_mixin(self) -> None:
        harness = _Harness()
        harness._pending_plan = _plan(400)
        harness.on_bar(SimpleNamespace(ts_event=500))
        self.assertEqual(harness.submitted, [])
        self.assertIsNotNone(harness._pending_plan)

    def test_new_plan_fails_closed_when_slot_is_not_flat(self) -> None:
        harness = _Harness()
        harness.portfolio.flat = False
        harness.plan_to_create = _plan(500)
        harness.on_bar(SimpleNamespace(ts_event=500))
        self.assertEqual(harness.submitted, [])
        self.assertEqual(
            harness.invalidated,
            [("IMMEDIATE_CAUSAL_ENTRY_SLOT_OR_WINDOW_LOST", 500)],
        )

    def test_new_plan_outside_trade_window_is_invalidated(self) -> None:
        harness = _Harness()
        harness.plan_to_create = _plan(1_000)
        harness.on_bar(SimpleNamespace(ts_event=1_000))
        self.assertEqual(harness.submitted, [])
        self.assertEqual(len(harness.invalidated), 1)


if __name__ == "__main__":
    unittest.main()
