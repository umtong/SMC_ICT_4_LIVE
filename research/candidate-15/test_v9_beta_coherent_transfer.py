from __future__ import annotations

from datetime import datetime, timezone
import inspect
from pathlib import Path
import unittest

from beta_coherent_transfer import (
    BetaCoherentResidualTransferContinuationEngine,
    BetaCoherentTransferPersistentQuarterHourRouter,
    BetaCoherentTransferState,
    V9_HORIZONS,
    _zero_intercept_beta,
)
from candidate15_portfolio_materializer import materialize_candidate15_portfolio_source
from candidate15_v6_residual_laggard_materializer import materialize_residual_laggard_source
from candidate15_v7_bounded_transfer_materializer import materialize_bounded_transfer_source
from candidate15_v8_managed_transfer_materializer import materialize_managed_transfer_source
from candidate15_v9_beta_transfer_materializer import materialize_beta_coherent_transfer_source
from logic import BarObs, Direction, LogicConfig, MINUTE_NS
from portfolio_materializer import materialize_combined_portfolio_source
from quarter_hour_persistent_initiative import SYMBOLS
from runner_materializer import materialize_runner_source


class BetaCoherentTransferTests(unittest.TestCase):
    @staticmethod
    def _bar(ts_ns: int, opening: float, closing: float, *, flow: float) -> BarObs:
        return BarObs(
            ts_ns,
            opening,
            max(opening, closing) + 0.01,
            min(opening, closing) - 0.01,
            closing,
            1000.0,
            500.0 * (1.0 + flow),
        )

    def test_zero_intercept_beta(self) -> None:
        x = [float(index) / 1000.0 for index in range(1, 33)]
        y = [0.75 * value for value in x]
        self.assertAlmostEqual(_zero_intercept_beta(x, y) or 0.0, 0.75, places=12)
        self.assertIsNone(_zero_intercept_beta(x[:7], y[:7]))

    def _run_router(self, receiver_multiplier: float) -> BetaCoherentTransferState | None:
        router = BetaCoherentTransferPersistentQuarterHourRouter(LogicConfig())
        prices = {symbol: 100.0 + index * 10.0 for index, symbol in enumerate(SYMBOLS)}
        start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        state = None
        # 210 minutes supplies >24 prior five-minute returns.  The synthetic
        # receiver has stable 0.75 beta to the three-sender median.
        for minute in range(1, 211):
            first_event = 181 <= minute <= 185
            second_event = 196 <= minute <= 200
            batch: dict[str, BarObs] = {}
            common = 0.018 if minute % 2 else -0.014
            for index, symbol in enumerate(SYMBOLS):
                opening = prices[symbol]
                sender = symbol != "XRPUSDT"
                if sender and (first_event or second_event):
                    drift = 0.18 + 0.01 * index
                    flow = 0.40
                elif symbol == "XRPUSDT" and (first_event or second_event):
                    drift = receiver_multiplier * 0.18
                    flow = 0.0
                else:
                    multiplier = 1.0 if sender else 0.75
                    drift = multiplier * common
                    flow = 0.05 if drift > 0 else -0.05
                closing = opening + drift
                ts_ns = start + minute * MINUTE_NS
                batch[symbol] = self._bar(ts_ns, opening, closing, flow=flow)
                prices[symbol] = closing
            state = router.on_batch(start + minute * MINUTE_NS, batch)
        return state

    def test_prior_beta_lag_creates_state(self) -> None:
        state = self._run_router(receiver_multiplier=0.25)
        self.assertIsInstance(state, BetaCoherentTransferState)
        assert isinstance(state, BetaCoherentTransferState)
        self.assertEqual(state.direction, Direction.LONG)
        self.assertEqual(tuple(sorted(state.beta_zero_intercept_by_horizon)), V9_HORIZONS)
        self.assertTrue(all(value > 0.0 for value in state.state_delivery_gap_by_horizon.values()))
        self.assertEqual(state.residual_symbol, "XRPUSDT")

    def test_receiver_that_keeps_up_fails_closed(self) -> None:
        state = self._run_router(receiver_multiplier=1.10)
        self.assertIsNone(state)

    def test_plan_filter_uses_exact_completed_mss_clock(self) -> None:
        source = inspect.getsource(BetaCoherentResidualTransferContinuationEngine._qualify_managed_transfer)
        self.assertIn("state.geometry_ts_ns != completed.end_ts_ns", source)
        self.assertIn("0.5 <= body_ratio < 1.0", source)
        self.assertIn("geometry_gaps", source)

    def test_full_materialization_compiles_and_routes_once(self) -> None:
        root = Path(__file__).resolve().parent
        candidate14 = root.parent / "candidate-14"
        source = (candidate14 / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        source = materialize_runner_source(source)
        source = materialize_combined_portfolio_source(source)
        source = materialize_candidate15_portfolio_source(source)
        source = materialize_residual_laggard_source(source)
        source = materialize_bounded_transfer_source(source)
        source = materialize_managed_transfer_source(source)
        source = materialize_beta_coherent_transfer_source(source)
        compile(source, str(candidate14 / "run_leadership_scdam_base.py"), "exec")
        required = {
            "BetaCoherentTransferPersistentQuarterHourRouter(": 1,
            "BetaCoherentResidualTransferContinuationEngine(": 1,
            "C15_V9_CORE_FAMILY_QUARANTINED": 3,
            "C15_V9_NOT_BETA_RECEIVER": 3,
            'continuation.details["candidate15_v9_ownership"]': 1,
            'self.active_plan.details.get("candidate15_v9_transfer")': 1,
        }
        for token, expected in required.items():
            self.assertEqual(source.count(token), expected, token)
        for stale in (
            "ManagedTransferPersistentQuarterHourRouter(",
            "ManagedResidualTransferContinuationEngine(",
            "C15_V8_CORE_FAMILY_QUARANTINED",
            'self.active_plan.details.get("candidate15_v8_transfer")',
        ):
            self.assertNotIn(stale, source)


if __name__ == "__main__":
    unittest.main()
