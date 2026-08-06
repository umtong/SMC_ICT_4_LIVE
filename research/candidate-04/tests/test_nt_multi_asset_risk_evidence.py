from __future__ import annotations

import unittest

import pandas as pd

import nt_multi_asset_risk_evidence as candidate


class MultiAssetRiskEvidenceTests(unittest.TestCase):
    def test_positions_are_matched_by_instrument_and_open_time(self) -> None:
        positions = pd.DataFrame(
            [
                {
                    "instrument_id": "ETHUSDT-PERP.BINANCE",
                    "ts_opened": 20,
                    "realized_pnl": "-200.0 USDT",
                },
                {
                    "instrument_id": "BTCUSDT-PERP.BINANCE",
                    "ts_opened": 30,
                    "realized_pnl": "100.0 USDT",
                },
                {
                    "instrument_id": "BTCUSDT-PERP.BINANCE",
                    "ts_opened": 10,
                    "realized_pnl": "-300.0 USDT",
                },
            ]
        )
        events = {
            "BTCUSDT": [
                {
                    "event_type": "ENTRY_SUBMITTED",
                    "event_timestamp": 30,
                    "details": {"equity": 12000.0},
                },
                {
                    "event_type": "ENTRY_SUBMITTED",
                    "event_timestamp": 10,
                    "details": {"equity": 10000.0},
                },
            ],
            "ETHUSDT": [
                {
                    "event_type": "ENTRY_SUBMITTED",
                    "event_timestamp": 20,
                    "details": {"equity": 11000.0},
                }
            ],
            "SOLUSDT": [],
            "XRPUSDT": [],
        }
        evidence = candidate.reconcile_risk_evidence(positions, events)
        self.assertTrue(evidence.pass_)
        self.assertEqual(evidence.matched_positions, 3)
        self.assertEqual(evidence.matched_entries, 3)
        self.assertEqual(evidence.matched_losses, 2)
        self.assertAlmostEqual(
            evidence.maximum_realized_loss_fraction,
            0.03,
        )
        self.assertEqual(
            evidence.ordering["BTCUSDT"],
            "instrument_then_ts_opened",
        )

    def test_loss_above_three_percent_fails(self) -> None:
        positions = pd.DataFrame(
            [
                {
                    "instrument_id": "BTCUSDT-PERP.BINANCE",
                    "ts_opened": 10,
                    "realized_pnl": "-301.1 USDT",
                }
            ]
        )
        events = {
            "BTCUSDT": [
                {
                    "event_type": "ENTRY_SUBMITTED",
                    "event_timestamp": 10,
                    "details": {"equity": 10000.0},
                }
            ],
            "ETHUSDT": [],
            "SOLUSDT": [],
            "XRPUSDT": [],
        }
        evidence = candidate.reconcile_risk_evidence(positions, events)
        self.assertFalse(evidence.pass_)
        self.assertGreater(
            evidence.maximum_realized_loss_fraction,
            0.0301,
        )

    def test_missing_instrument_column_fails_closed(self) -> None:
        positions = pd.DataFrame(
            [{"ts_opened": 10, "realized_pnl": "-100 USDT"}]
        )
        events = {
            "BTCUSDT": [
                {
                    "event_type": "ENTRY_SUBMITTED",
                    "event_timestamp": 10,
                    "details": {"equity": 10000.0},
                }
            ],
            "ETHUSDT": [],
            "SOLUSDT": [],
            "XRPUSDT": [],
        }
        evidence = candidate.reconcile_risk_evidence(positions, events)
        self.assertFalse(evidence.pass_)
        self.assertTrue(evidence.errors)

    def test_symbol_count_mismatch_fails_closed(self) -> None:
        positions = pd.DataFrame(
            [
                {
                    "instrument_id": "SOLUSDT-PERP.BINANCE",
                    "ts_opened": 10,
                    "realized_pnl": "-100 USDT",
                }
            ]
        )
        events = {
            "BTCUSDT": [],
            "ETHUSDT": [],
            "SOLUSDT": [],
            "XRPUSDT": [],
        }
        evidence = candidate.reconcile_risk_evidence(positions, events)
        self.assertFalse(evidence.pass_)
        self.assertIn("SOLUSDT", " ".join(evidence.errors))


if __name__ == "__main__":
    unittest.main()
