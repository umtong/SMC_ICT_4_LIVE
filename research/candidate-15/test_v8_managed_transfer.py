from __future__ import annotations

from pathlib import Path
import unittest

from bounded_transfer_initiative import BoundedTransferInitiativeState
from candidate15_portfolio_materializer import materialize_candidate15_portfolio_source
from candidate15_v6_residual_laggard_materializer import materialize_residual_laggard_source
from candidate15_v7_bounded_transfer_materializer import materialize_bounded_transfer_source
from candidate15_v8_managed_transfer_materializer import materialize_managed_transfer_source
from logic import Direction, LogicConfig, Scenario, StructuralBar, TradePlan
from managed_transfer_initiative import ManagedResidualTransferContinuationEngine, V8_MODULE
from portfolio_materializer import materialize_combined_portfolio_source
from runner_materializer import materialize_runner_source


class V8ManagedTransferTests(unittest.TestCase):
    @staticmethod
    def state() -> BoundedTransferInitiativeState:
        return BoundedTransferInitiativeState(
            scenario_id="STATE-1", direction=Direction.LONG,
            activated_ts_ns=1_000, expires_ts_ns=20_000,
            owner_symbol="BTCUSDT",
            accepted_symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
            origins={"BTCUSDT": 100.0, "ETHUSDT": 100.0, "SOLUSDT": 100.0},
            source_event_ids=("E1", "E2"), confirmation_span_ns=1_000,
            overlap_symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
            median_directional_progress=0.02,
            advancing_symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
            origin_holding_symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
            effective_ts_ns=1_000, evidence_event_ids=("E1", "E2"),
            residual_symbol="XRPUSDT", residual_reference_price=100.0,
            residual_confirmation_price=100.5,
            residual_directional_progress=0.005, delivery_gap=0.015,
            accepted_min_standardized_body=0.4,
            accepted_median_standardized_body=0.6, parity_price=102.0,
        )

    @staticmethod
    def plan() -> TradePlan:
        entry, stop, target = 100.0, 99.0, 105.0
        loss = (entry - stop) + entry * 0.0004 + stop * 0.0008
        gain = (target - entry) - entry * 0.0004 - target * 0.0004
        return TradePlan(
            scenario_id="PLAN-1", scenario=Scenario.AAC, direction=Direction.LONG,
            observed_ts_ns=6_000, expected_entry=entry, stop_price=stop,
            target_price=target, atr=1.0, loss_per_unit=loss,
            gain_per_unit=gain, net_r=gain / loss, reason_code="TEST",
            expire_ts_ns=12_000,
            details={"target_model": "NEXT_LIVE_EXTERNAL_4H_OR_PREVIOUS_DAY_POOL"},
        )

    @staticmethod
    def bar(high: float, close: float, end: int = 5_000) -> StructuralBar:
        return StructuralBar(
            start_ts_ns=end - 1_000, end_ts_ns=end, open=100.5,
            high=high, low=100.0, close=close, volume=1000.0,
            taker_buy_volume=700.0, high_ts_ns=end, low_ts_ns=end - 500,
        )

    def engine(self) -> ManagedResidualTransferContinuationEngine:
        return ManagedResidualTransferContinuationEngine(
            LogicConfig(), "XRPUSDT-PERP.BINANCE", symbol="XRPUSDT", logic_key="TEST",
        )

    def test_partial_catch_up_and_handoff_are_distinct(self) -> None:
        first, second = self.plan(), self.plan()
        self.assertIs(self.engine()._qualify_managed_transfer(first, self.state(), self.bar(101.5, 101.2)), first)
        self.assertEqual(first.details["candidate15_v8_transfer"]["stage"], "PARTIAL_CATCH_UP")
        self.assertEqual(first.details["module"], V8_MODULE)
        self.assertIs(self.engine()._qualify_managed_transfer(second, self.state(), self.bar(102.5, 102.2)), second)
        self.assertEqual(second.details["candidate15_v8_transfer"]["stage"], "PARITY_HANDOFF_RETEST")

    def test_prior_parity_consumption_fails_closed(self) -> None:
        engine = self.engine()
        engine._bars.append(self.bar(102.1, 101.0, 3_000))
        self.assertIsNone(engine._qualify_managed_transfer(self.plan(), self.state(), self.bar(102.5, 102.2)))
        self.assertEqual(engine.skips["QHI_V8_TRANSFER_STAGE_UNRESOLVED"], 1)

    def test_full_materialization_compiles_and_isolated(self) -> None:
        root = Path(__file__).resolve().parent
        base = root.parent / "candidate-14" / "run_leadership_scdam_base.py"
        source = base.read_text(encoding="utf-8")
        for transform in (
            materialize_runner_source,
            materialize_combined_portfolio_source,
            materialize_candidate15_portfolio_source,
            materialize_residual_laggard_source,
            materialize_bounded_transfer_source,
            materialize_managed_transfer_source,
        ):
            source = transform(source)
        compile(source, str(base), "exec")
        for token, expected in {
            "ManagedTransferPersistentQuarterHourRouter(": 1,
            "ManagedResidualTransferContinuationEngine(": 1,
            "C15_V8_CORE_FAMILY_QUARANTINED": 3,
            "C15_V8_NOT_RESIDUAL_RECEIVER": 3,
            'continuation.details["candidate15_v8_ownership"]': 1,
            "TRANSFER_STOP_MODIFICATION_SUBMITTED": 1,
            "TRANSFER_PROTECTIVE_STOP_NOT_UNIQUE": 1,
            "PROTECTIVE_ORDER_DENIED_FAIL_CLOSED": 1,
            "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED": 1,
            "self._protect_completed_transfer(ts_ns)": 1,
        }.items():
            self.assertEqual(source.count(token), expected, token)
        for stale in (
            "BoundedTransferPersistentQuarterHourRouter(",
            "BoundedResidualTransferContinuationEngine(",
            "C15_V7_CORE_FAMILY_QUARANTINED",
            "C15_V7_NOT_RESIDUAL_RECEIVER",
            'continuation.details["candidate15_v7_ownership"]',
        ):
            self.assertNotIn(stale, source)


if __name__ == "__main__":
    unittest.main()
