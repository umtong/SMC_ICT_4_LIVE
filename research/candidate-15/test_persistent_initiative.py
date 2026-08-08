from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from candidate15_portfolio_materializer import materialize_candidate15_portfolio_source
from logic import BarObs, Direction, LogicConfig, MINUTE_NS
from portfolio_materializer import materialize_combined_portfolio_source
from quarter_hour_persistent_initiative import PersistentQuarterHourRouter, SYMBOLS
from runner_materializer import materialize_runner_source


class PersistentInitiativeRouterTests(unittest.TestCase):
    def test_two_distinct_common_flow_events_activate_and_origin_reacceptance_terminates(self) -> None:
        config = LogicConfig()
        router = PersistentQuarterHourRouter(config)
        prices = {symbol: 100.0 + 10.0 * index for index, symbol in enumerate(SYMBOLS)}
        start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        state = None
        for minute in range(1, 51):
            batch: dict[str, BarObs] = {}
            strong_window = 31 <= minute <= 35 or 46 <= minute <= 50
            for index, symbol in enumerate(SYMBOLS):
                opening = prices[symbol]
                accepted = symbol != "XRPUSDT"
                drift = 0.16 + index * 0.01 if strong_window and accepted else 0.001
                closing = opening + drift
                batch[symbol] = BarObs(
                    start + minute * MINUTE_NS,
                    opening,
                    max(opening, closing) + 0.01,
                    min(opening, closing) - 0.01,
                    closing,
                    1000.0,
                    700.0 if strong_window and accepted else 500.0,
                )
                prices[symbol] = closing
            state = router.on_batch(start + minute * MINUTE_NS, batch)

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.direction, Direction.LONG)
        self.assertGreaterEqual(len(state.accepted_symbols), 3)
        self.assertEqual(len(state.source_event_ids), 2)

        origins = dict(state.origins)
        for minute in range(51, 56):
            batch = {}
            for symbol in SYMBOLS:
                opening = prices[symbol]
                if minute == 55 and symbol in state.accepted_symbols:
                    closing = origins[symbol] - 0.10
                else:
                    closing = opening + 0.001
                batch[symbol] = BarObs(
                    start + minute * MINUTE_NS,
                    opening,
                    max(opening, closing) + 0.01,
                    min(opening, closing) - 0.01,
                    closing,
                    1000.0,
                    500.0,
                )
                prices[symbol] = closing
            state = router.on_batch(start + minute * MINUTE_NS, batch)
        self.assertIsNone(state)
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
            "PersistentQuarterHourRouter(logic_config)": 1,
            "PersistentInitiativeContinuationEngine(": 1,
            "plans.append((continuation, continuation_candidate))": 1,
            "C15_V4_CORE_FAMILY_QUARANTINED": 3,
            "C15_UNROUTED_SCENARIO_FAMILY": 3,
            "candidate-15-v4-protective-rejection-fail-close": 1,
        }.items():
            self.assertEqual(source.count(token), expected, token)


if __name__ == "__main__":
    unittest.main()
