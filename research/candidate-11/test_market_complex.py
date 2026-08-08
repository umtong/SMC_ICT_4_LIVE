from decimal import Decimal
import unittest

from market_complex import BoundarySide, ComplexObservation, MarketComplex, SourceRange


def obs(symbol: str, *, ts: int = 1, high: str, low: str, close: str, flow: str = "0") -> ComplexObservation:
    return ComplexObservation(
        symbol=symbol,
        ts_ns=ts,
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        signed_flow=Decimal(flow),
        source_range=SourceRange(Decimal("90"), Decimal("100")),
    )


class MarketComplexTests(unittest.TestCase):
    def test_idiosyncratic_high_raid_is_far_nonconfirmation(self) -> None:
        snapshot = {
            "BTCUSDT": obs("BTCUSDT", high="103", low="98", close="99"),
            "ETHUSDT": obs("ETHUSDT", high="100.5", low="97", close="99"),
            "SOLUSDT": obs("SOLUSDT", high="99.7", low="96", close="98"),
            "XRPUSDT": obs("XRPUSDT", high="99.9", low="96", close="98"),
        }
        result = MarketComplex().evaluate(snapshot, symbol="BTCUSDT", side=BoundarySide.HIGH)
        self.assertTrue(result.far_nonconfirmation)
        self.assertFalse(result.aac_breadth_confirmation)

    def test_broad_high_acceptance_confirms_aac(self) -> None:
        snapshot = {
            "BTCUSDT": obs("BTCUSDT", high="103", low="100", close="102"),
            "ETHUSDT": obs("ETHUSDT", high="102", low="99", close="101"),
            "SOLUSDT": obs("SOLUSDT", high="102", low="99", close="100.5"),
            "XRPUSDT": obs("XRPUSDT", high="100", low="97", close="99"),
        }
        result = MarketComplex().evaluate(snapshot, symbol="BTCUSDT", side=BoundarySide.HIGH)
        self.assertFalse(result.far_nonconfirmation)
        self.assertTrue(result.aac_breadth_confirmation)
        self.assertEqual(result.same_side_outside_closes, 3)

    def test_future_or_asynchronous_snapshot_is_rejected(self) -> None:
        snapshot = {
            "BTCUSDT": obs("BTCUSDT", ts=1, high="101", low="98", close="99"),
            "ETHUSDT": obs("ETHUSDT", ts=2, high="101", low="98", close="99"),
            "SOLUSDT": obs("SOLUSDT", ts=1, high="101", low="98", close="99"),
        }
        with self.assertRaisesRegex(ValueError, "one completed timestamp"):
            MarketComplex().evaluate(snapshot, symbol="BTCUSDT", side=BoundarySide.HIGH)

    def test_requires_three_markets(self) -> None:
        snapshot = {
            "BTCUSDT": obs("BTCUSDT", high="101", low="98", close="99"),
            "ETHUSDT": obs("ETHUSDT", high="101", low="98", close="99"),
        }
        with self.assertRaisesRegex(ValueError, "at least three"):
            MarketComplex().evaluate(snapshot, symbol="BTCUSDT", side=BoundarySide.HIGH)


if __name__ == "__main__":
    unittest.main()
