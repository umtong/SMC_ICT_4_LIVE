from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from strategy_clock_alert_entry import ClockAlertCausalEntryMixin  # noqa: E402


class _Clock:
    def __init__(self) -> None:
        self.alerts = {}
        self.canceled = []

    def set_time_alert_ns(
        self,
        name: str,
        alert_time_ns: int,
        callback,
        allow_past: bool,
    ) -> None:
        self.alerts[name] = (alert_time_ns, callback, allow_past)

    def cancel_timer(self, name: str) -> None:
        self.canceled.append(name)
        self.alerts.pop(name, None)

    def fire(self, name: str) -> None:
        timestamp, callback, _ = self.alerts.pop(name)
        callback(SimpleNamespace(name=name, ts_event=timestamp))


class _Portfolio:
    def __init__(self) -> None:
        self.flat = True

    def is_flat(self, instrument_id) -> bool:
        del instrument_id
        return self.flat


class _BaseHarness:
    def __init__(self, config=None) -> None:
        del config
        self.config = SimpleNamespace(
            trade_start_ns=100,
            trade_end_ns=1_000,
            instrument_id="BTCUSDT.BINANCE",
        )
        self.clock = _Clock()
        self.portfolio = _Portfolio()
        self._pending_plan = None
        self._active_plan = None
        self._exit_pending = False
        self.plan_to_create = None
        self.submitted = []
        self.invalidated = []
        self.events = []

    def on_bar(self, bar) -> None:
        if self.plan_to_create is not None:
            self._pending_plan = self.plan_to_create
            self.plan_to_create = None

    def on_stop(self) -> None:
        pass

    def _submit_pending(self, bar) -> None:
        self.submitted.append(
            (self._pending_plan.scenario_id, int(bar.ts_event))
        )
        self._active_plan = self._pending_plan
        self._pending_plan = None

    def _invalidate_pending(self, reason: str, event_time_ns: int) -> None:
        self.invalidated.append((reason, event_time_ns))
        self._pending_plan = None

    def _append_manual_event(self, **kwargs) -> None:
        self.events.append(kwargs)


class _Harness(ClockAlertCausalEntryMixin, _BaseHarness):
    def __init__(self) -> None:
        super().__init__(None)


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


class ClockAlertEntryTests(unittest.TestCase):
    def test_plan_is_scheduled_strictly_after_observation(self) -> None:
        harness = _Harness()
        harness.plan_to_create = _plan(500)
        harness.on_bar(SimpleNamespace(ts_event=500))

        self.assertEqual(harness.submitted, [])
        self.assertEqual(len(harness.clock.alerts), 1)
        name = next(iter(harness.clock.alerts))
        timestamp, _, allow_past = harness.clock.alerts[name]
        self.assertEqual(timestamp, 501)
        self.assertFalse(allow_past)
        self.assertEqual(
            harness.events[-1]["reason_code"],
            "CAUSAL_ENTRY_ALERT_SCHEDULED",
        )

    def test_alert_submits_the_same_plan_without_new_bar_data(self) -> None:
        harness = _Harness()
        harness.plan_to_create = _plan(500)
        signal_bar = SimpleNamespace(ts_event=500)
        harness.on_bar(signal_bar)
        name = next(iter(harness.clock.alerts))
        harness.clock.fire(name)

        self.assertEqual(harness.submitted, [("scenario-500", 500)])
        self.assertIsNone(harness._pending_plan)
        self.assertEqual(
            harness.events[-1]["reason_code"],
            "CAUSAL_ENTRY_ALERT_FIRED",
        )
        self.assertEqual(
            harness.events[-1]["event_time_ns"],
            501,
        )

    def test_surviving_alert_at_next_bar_fails_closed(self) -> None:
        harness = _Harness()
        harness.plan_to_create = _plan(500)
        harness.on_bar(SimpleNamespace(ts_event=500))
        with self.assertRaises(RuntimeError):
            harness.on_bar(SimpleNamespace(ts_event=600))
        self.assertEqual(
            harness.invalidated,
            [("CAUSAL_ENTRY_ALERT_DID_NOT_FIRE_BEFORE_NEXT_BAR", 600)],
        )
        self.assertEqual(harness.submitted, [])

    def test_slot_loss_before_alert_invalidates_plan(self) -> None:
        harness = _Harness()
        harness.plan_to_create = _plan(500)
        harness.on_bar(SimpleNamespace(ts_event=500))
        name = next(iter(harness.clock.alerts))
        harness.portfolio.flat = False
        harness.clock.fire(name)
        self.assertEqual(harness.submitted, [])
        self.assertEqual(
            harness.invalidated,
            [("CAUSAL_ENTRY_ALERT_SLOT_OR_WINDOW_LOST_AT_FIRE", 501)],
        )


if __name__ == "__main__":
    unittest.main()
