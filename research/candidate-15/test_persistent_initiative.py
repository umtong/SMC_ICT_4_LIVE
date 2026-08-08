from __future__ import annotations

from datetime import datetime, timezone
import inspect
from pathlib import Path
import unittest

from bounded_transfer_initiative import (
    BoundedResidualTransferContinuationEngine,
    BoundedTransferInitiativeState,
    BoundedTransferPersistentQuarterHourRouter,
)
from candidate15_portfolio_materializer import materialize_candidate15_portfolio_source
from candidate15_v6_residual_laggard_materializer import (
    materialize_residual_laggard_source,
    residual_laggard_symbol,
)
from candidate15_v7_bounded_transfer_materializer import (
    materialize_bounded_transfer_source,
)
from logic import BarObs, Direction, LogicConfig, MINUTE_NS
from portfolio_materializer import materialize_combined_portfolio_source
from quarter_hour_persistent_initiative import SYMBOLS
from runner_materializer import materialize_runner_source


class BoundedTransferInitiativeTests(unittest.TestCase):
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
        router = BoundedTransferPersistentQuarterHourRouter(config)
        prices = {symbol: 100.0 + 10.0 * index for index, symbol in enumerate(SYMBOLS)}
        start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        state = None
        # Warm 36 completed five-minute bars, then create two distinct
        # three-market events. XRP stays outside the sender set.
        for minute in range(1, 201):
            first_window = 181 <= minute <= 185
            second_window = 196 <= minute <= 200
            between = 186 <= minute <= 195
            batch: dict[str, BarObs] = {}
            for index, symbol in enumerate(SYMBOLS):
                sender = symbol != "XRPUSDT"
                opening = prices[symbol]
                if sender and (first_window or second_window):
                    drift = 0.16 + 0.01 * index
                    strong = True
                elif sender and between and reverse_between_events:
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

    def test_three_sender_response_creates_one_bounded_residual_state(self) -> None:
        router, state, _, _ = self._run(reverse_between_events=False)
        self.assertIsInstance(state, BoundedTransferInitiativeState)
        assert isinstance(state, BoundedTransferInitiativeState)
        self.assertEqual(state.direction, Direction.LONG)
        self.assertEqual(state.accepted_symbols, ("BTCUSDT", "ETHUSDT", "SOLUSDT"))
        self.assertEqual(state.residual_symbol, "XRPUSDT")
        self.assertGreater(state.delivery_gap, 0.0)
        self.assertGreater(state.accepted_min_standardized_body, 0.0)
        self.assertGreater(state.parity_price, state.residual_confirmation_price)
        self.assertEqual(state.effective_ts_ns, state.activated_ts_ns)
        self.assertEqual(state.confirmation_span_ns, 15 * MINUTE_NS)
        self.assertEqual(state.expires_ts_ns, state.effective_ts_ns + 15 * MINUTE_NS)
        self.assertTrue(
            any(
                event.reason_code == "THREE_SENDER_ONE_RESIDUAL_TRANSFER_STATE"
                for event in router.events
            ),
        )

    def test_same_direction_bodies_without_common_progress_remain_unresolved(self) -> None:
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
        assert isinstance(state, BoundedTransferInitiativeState)
        observed = state
        for minute in range(201, 206):
            batch = {}
            for symbol in SYMBOLS:
                opening = prices[symbol]
                if minute == 205 and symbol in state.accepted_symbols:
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

    def test_residual_identity_fails_closed(self) -> None:
        self.assertEqual(
            residual_laggard_symbol(("BTCUSDT", "ETHUSDT", "SOLUSDT")),
            "XRPUSDT",
        )
        self.assertIsNone(residual_laggard_symbol(SYMBOLS))
        self.assertIsNone(residual_laggard_symbol(("BTCUSDT", "ETHUSDT")))
        self.assertIsNone(
            residual_laggard_symbol(("BTCUSDT", "ETHUSDT", "DOGEUSDT")),
        )
        self.assertIsNone(
            residual_laggard_symbol(("BTCUSDT", "ETHUSDT", "ETHUSDT")),
        )

    def test_residual_atr_is_appended_after_plan_evaluation(self) -> None:
        source = inspect.getsource(BoundedResidualTransferContinuationEngine.on_bar)
        self.assertLess(
            source.index("plan = self._build_plan("),
            source.index("self._ranges.append("),
        )

    def test_full_candidate15_materialization_compiles_and_routes_once(self) -> None:
        root = Path(__file__).resolve().parent
        candidate14 = root.parent / "candidate-14"
        source = (candidate14 / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        source = materialize_runner_source(source)
        source = materialize_combined_portfolio_source(source)
        source = materialize_candidate15_portfolio_source(source)
        source = materialize_residual_laggard_source(source)
        source = materialize_bounded_transfer_source(source)
        compile(source, str(candidate14 / "run_leadership_scdam_base.py"), "exec")
        for token, expected in {
            "BoundedTransferPersistentQuarterHourRouter(": 1,
            "BoundedResidualTransferContinuationEngine(": 1,
            "plans.append((continuation, continuation_candidate))": 1,
            "C15_V7_CORE_FAMILY_QUARANTINED": 3,
            "C15_V7_NOT_RESIDUAL_RECEIVER": 3,
            'continuation.details["candidate15_v7_ownership"]': 1,
            "C15_UNROUTED_SCENARIO_FAMILY": 3,
            "PASSIVE_ENTRY_REJECTED_UNFILLED": 3,
            "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED": 1,
        }.items():
            self.assertEqual(source.count(token), expected, token)
        for stale in (
            "ResponseQualifiedPersistentQuarterHourRouter(",
            "PersistentInitiativeContinuationEngine(",
            "C15_V6_CORE_FAMILY_QUARANTINED",
            "C15_V6_NOT_RESIDUAL_LAGGARD",
            'continuation.details["candidate15_v6_route"]',
        ):
            self.assertNotIn(stale, source)


if __name__ == "__main__":
    unittest.main()
