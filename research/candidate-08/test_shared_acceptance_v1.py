from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_acceptance_causal_v1 import AcceptanceSignal, acceptance_structural_stop
from aggtrade_orderflow_probe import PendingEvent
from range_fvg_logic import ExternalLevel, LevelKind, LevelSource


def _boundary() -> ExternalLevel:
    return ExternalLevel(
        level_id="completed-four-hour-high",
        kind=LevelKind.HIGH,
        source=LevelSource.FOUR_HOUR,
        level=100.0,
        formed_index=1,
        formed_time_ns=1,
        period_key="completed-period",
    )


def _pending(direction: int) -> PendingEvent:
    return PendingEvent(
        scenario_id="acceptance-test",
        family="BREAKOUT_ACCEPTANCE_CONTINUATION",
        trade_direction=direction,
        outward_direction=direction,
        boundary=_boundary(),
        armed_position=10,
        expiry_position=20,
        extreme=103.0 if direction > 0 else 97.0,
        reference_high=103.0,
        reference_low=97.0,
        displacement_volume=200.0,
        displacement_trade_count=100.0,
        displacement_imbalance=0.5 * direction,
        retest_position=12,
        retest_high=101.2,
        retest_low=100.4,
        retest_volume=80.0,
        retest_trade_count=50.0,
    )


class SharedAcceptanceContractTests(unittest.TestCase):
    def test_long_stop_uses_observed_retest_low(self) -> None:
        item = _pending(1)
        stop, source = acceptance_structural_stop(
            item, direction=1, entry=101.5, atr=1.0,
        )
        self.assertAlmostEqual(stop, 100.37)
        self.assertEqual(source, "ACCEPTANCE_RETEST_LOW")
        self.assertNotEqual(stop, item.extreme)

    def test_short_stop_uses_observed_retest_high(self) -> None:
        item = _pending(-1)
        stop, source = acceptance_structural_stop(
            item, direction=-1, entry=99.0, atr=1.0,
        )
        self.assertAlmostEqual(stop, 101.23)
        self.assertEqual(source, "ACCEPTANCE_RETEST_HIGH")
        self.assertNotEqual(stop, item.extreme)

    def test_signal_schema_has_no_future_outcome(self) -> None:
        names = {field.name for field in fields(AcceptanceSignal)}
        forbidden = {
            "outcome", "outcome_240m", "outcome_time", "net_r_proxy_240m",
            "net_mfe_240m_r", "net_mae_240m_r", "future",
        }
        self.assertTrue(names.isdisjoint(forbidden))

    def test_source_never_calls_first_touch(self) -> None:
        source = (HERE / "aggtrade_acceptance_causal_v1.py").read_text(encoding="utf-8")
        for forbidden in ("_first_touch", "outcome_240m", "net_mfe_240m", "net_mae_240m"):
            self.assertNotIn(forbidden, source)

    def test_strategy_waits_for_all_assets_and_uses_market_entry(self) -> None:
        source = (HERE / "aggtrade_acceptance_shared_strategy_v1.py").read_text(encoding="utf-8")
        self.assertIn(
            "self.seen_instruments_at_timestamp == self.expected_instrument_ids",
            source,
        )
        self.assertIn("entry_order_type=OrderType.MARKET", source)
        self.assertIn("GLOBAL_PORTFOLIO_OR_ORDER_UNAVAILABLE", source)
        self.assertIn("risk_sized_quantity", source)


if __name__ == "__main__":
    unittest.main()
