from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from funding_evidence_v2 import write_funding_evidence
from funding_module import HistoricalFundingBoundary, NS_PER_MINUTE
from instruments import make_instrument


class _FundingModule:
    def __init__(self, ledger: list[dict[str, object]]) -> None:
        self.ledger = ledger
        self.boundaries = [object(), object()]
        self.processed_boundaries = 2
        self.settled_positions = 2


class FundingEvidenceTests(unittest.TestCase):
    def test_millisecond_archive_time_maps_to_containing_minute(self) -> None:
        instrument = make_instrument("BTCUSDT")
        minute = 1_706_976_000_000_000_000
        boundary = HistoricalFundingBoundary(
            symbol="BTCUSDT",
            instrument_id=instrument.id,
            funding_time_ns=minute + 1_000_000,
            interval_minutes=480,
            rate=Decimal("0.0001"),
            mark_price=Decimal("43000"),
        )
        self.assertEqual(boundary.funding_time_ns, minute + 1_000_000)
        self.assertEqual(boundary.settlement_time_ns, minute)
        self.assertEqual(boundary.settlement_time_ns % NS_PER_MINUTE, 0)

    def test_recycled_netting_position_id_is_joined_by_open_trade_interval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            audit = pd.DataFrame(
                [
                    {
                        "position_id": "BTC-POSITION-old-snapshot",
                        "plan_id": "PLAN-1",
                        "opening_order_id": "ENTRY-1",
                        "instrument_id": "BTCUSDT-PERP.BINANCE",
                        "side": "LONG",
                        "quantity": 2.0,
                        "ts_opened": "2024-02-01T00:00:00Z",
                        "ts_closed": "2024-02-01T10:00:00Z",
                        "realized_pnl": 10.0,
                        "risk_budget": 100.0,
                    },
                    {
                        "position_id": "BTC-POSITION",
                        "plan_id": "PLAN-2",
                        "opening_order_id": "ENTRY-2",
                        "instrument_id": "BTCUSDT-PERP.BINANCE",
                        "side": "SHORT",
                        "quantity": 3.0,
                        "ts_opened": "2024-02-02T00:00:00Z",
                        "ts_closed": "2024-02-02T10:00:00Z",
                        "realized_pnl": -5.0,
                        "risk_budget": 100.0,
                    },
                ],
            )
            audit.to_csv(output / "trade_audit.csv", index=False)
            first_ns = int(pd.Timestamp("2024-02-01T08:00:00Z").value)
            second_ns = int(pd.Timestamp("2024-02-02T08:00:00Z").value)
            common = {
                "symbol": "BTCUSDT",
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "position_id": "BTC-POSITION",
                "account_id": "BINANCE-001",
                "strategy_id": "TEST-001",
                "interval_minutes": 480,
                "rate": "0.01",
                "mark_price": "100",
                "notional": "200",
                "currency": "USDT",
            }
            module = _FundingModule(
                [
                    {
                        **common,
                        "funding_time_ns": first_ns,
                        "settlement_time_ns": first_ns,
                        "processed_time_ns": first_ns,
                        "signed_qty": "2",
                        "amount": "-1",
                    },
                    {
                        **common,
                        "funding_time_ns": second_ns,
                        "settlement_time_ns": second_ns,
                        "processed_time_ns": second_ns,
                        "signed_qty": "-3",
                        "notional": "300",
                        "amount": "2",
                    },
                ],
            )
            metrics = {
                "starting_nav": 100.0,
                "final_nav": 106.0,
            }
            evidence = write_funding_evidence(module, output, metrics)
            result = pd.read_csv(output / "trade_audit.csv")
            ledger = pd.read_csv(output / "funding_ledger.csv")
            with (output / "funding_ledger.jsonl").open(encoding="utf-8") as stream:
                records = [json.loads(line) for line in stream]

        self.assertEqual(evidence["funding_engine_position_ids"], 1)
        self.assertEqual(evidence["funding_positions_charged_or_credited"], 2)
        self.assertEqual(evidence["unmatched_funding_position_ids"], [])
        self.assertAlmostEqual(evidence["nav_reconciliation_error"], 0.0)
        self.assertEqual(result["funding_pnl"].tolist(), [-1.0, 2.0])
        self.assertEqual(result["realized_pnl_after_funding"].tolist(), [9.0, -3.0])
        self.assertEqual(
            ledger["trade_position_id"].tolist(),
            ["BTC-POSITION-old-snapshot", "BTC-POSITION"],
        )
        self.assertEqual([item["opening_order_id"] for item in records], ["ENTRY-1", "ENTRY-2"])


if __name__ == "__main__":
    unittest.main()
