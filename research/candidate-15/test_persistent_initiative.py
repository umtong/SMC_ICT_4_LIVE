from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from candidate15_portfolio_materializer import materialize_candidate15_portfolio_source
from logic import BarObs, Direction, LogicConfig, MINUTE_NS
from portfolio_materializer import materialize_combined_portfolio_source
from quarter_hour_persistent_initiative import SYMBOLS
from response_qualified_persistent_initiative import (
    ResponseQualifiedPersistentQuarterHourRouter,
)
from runner_materializer import materialize_runner_source


class ResponseQualifiedInitiativeTests(unittest.TestCase):
    @staticmethod
    def _bar(ts_ns: int, opening: float, closing: float, *, strong: bool) -> BarObs:
        return BarObs(
            ts_ns,
            opening,
            max(opening, closing) + 0.01,
            min(opening, closing) - 0.01,
            closing,
            1000.0,
            700.0 if strong else 500.0,
        )

    def _run(self, *, reverse_between_events: bool):
        config = LogicConfig()
        router = ResponseQualifiedPersistentQuarterHourRouter(config)
        prices = {symbol: 100.0 + 10.0 * index for index, symbol in enumerate(SYMBOLS)}
        start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        state = None
        for minute in range(1, 141):
            first_window = 121 <= minute <= 125
            second_window = 136 <= minute <= 140
            between = 126 <= minute <= 135
            batch: dict[str, BarObs] = {}
            for index, symbol in enumerate(SYMBOLS):
                accepted = symbol != "XRPUSDT"
                opening = prices[symbol]
                if accepted and (first_window or second_window):
                    drift = 0.16 + 0.01 * index
                    strong = True
                elif accepted and between and reverse_between_events:
                    drift = -0.10
                    strong = False
                else:
                    drift = 0.001 if minute % 2 else -0.001
                    strong = False
                closing = opening + drift
                batch[symbol] = self._bar(
                    start + minute * MINUTE_NS,
                    opening,
                    closing,
                    strong=strong,
                )
                prices[symbol] = closing
            state = router.on_batch(start + minute * MINUTE_NS, batch)
        return router, state, prices, start

    def test_timeframe_consistent_events_with_common_progress_activate(self) -> None:
        router, state, _, _ = self._run(reverse_between_events=False)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.direction, Direction.LONG)
        self.assertGreaterEqual(len(state.accepted_symbols), 3)
        self.assertGreater(state.median_directional_progress, 0.0)
        self.assertEqual(state.confirmation_span_ns, 15 * MINUTE_NS)
        self.assertEqual(state.expires_ts_ns, state.activated_ts_ns + 15 * MINUTE_NS)
        self.assertTrue(
            any(
                event.reason_code == "TIMEFRAME_CONSISTENT_COMMON_FLOW_RESPONSE_CONFIRMED"
                for event in router.events
            ),
        )

    def test_same_direction_bodies_without_inter_event_progress_remain_unresolved(self) -> None:
        router, state, _, _ = self._run(reverse_between_events=True)
        self.assertIsNone(state)
        self.assertGreater(
            router.skips["QHI_V5_SAME_DIRECTION_EVENT_LACKED_PERSISTENT_RESPONSE"],
            0,
        )
        self.assertTrue(
            any(event.event_type == "QHI_RESPONSE_REJECTED" for event in router.events),
        )

    def test_active_state_terminates_on_majority_latest_origin_reacceptance(self) -> None:
        router, state, prices, start = self._run(reverse_between_events=False)
        assert state is not None
        for minute in range(141, 146):
            batch = {}
            for symbol in SYMBOLS:
                opening = prices[symbol]
                if minute == 145 and symbol in state.accepted_symbols:
                    closing = float(state.origins[symbol]) - 0.10
                else:
                    closing = opening
                batch[symbol] = self._bar(
                    start + minute * MINUTE_NS,
                    opening,
                    closing,
                    strong=False,
                )
                prices[symbol] = closing
            observed = router.on_batch(start + minute * MINUTE_NS, batch)
        self.assertIsNone(observed)
        self.assertTrue(
            any(
                event.reason_code == "MAJORITY_CONFIRMING_ORIGINS_REACCEPTED"
                for event in router.events
            ),
        )

    def test_full_candidate15_materialization_compiles_and_routes_once(self) -> None:
        root = Path(__file__).resolve().parent
        candidate14 = root.parent / "candidate-14"
        source = (candidate14 / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        source = materialize_runner_source(source)
        source = materialize_combined_portfolio_source(source)
        source = materialize_candidate15_portfolio_source(source)
        compile(source, str(candidate14 / "run_leadership_scdam_base.py"), "exec")
        for token, expected in {
            "ResponseQualifiedPersistentQuarterHourRouter(": 1,
            "PersistentInitiativeContinuationEngine(": 1,
            "plans.append((continuation, continuation_candidate))": 1,
            "C15_V5_CORE_FAMILY_QUARANTINED": 3,
            "C15_UNROUTED_SCENARIO_FAMILY": 3,
            "PASSIVE_ENTRY_REJECTED_UNFILLED": 3,
            "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED": 1,
        }.items():
            self.assertEqual(source.count(token), expected, token)


if __name__ == "__main__":
    unittest.main()
