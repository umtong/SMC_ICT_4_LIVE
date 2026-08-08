from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
from math import pow
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aggregate import SAFETY_KEYS, aggregate

UTC = timezone.utc


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_position(path: Path, *, symbol: str, direction: str, opened: datetime) -> None:
    fields = [
        "instrument_id",
        "entry",
        "ts_init",
        "ts_opened",
        "ts_last",
        "ts_closed",
        "realized_pnl",
    ]
    closed = opened + timedelta(hours=2)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "instrument_id": f"{symbol}-PERP.BINANCE",
                "entry": "BUY" if direction == "LONG" else "SELL",
                "ts_init": int(opened.timestamp() * 1_000_000_000),
                "ts_opened": opened.isoformat(),
                "ts_last": int(closed.timestamp() * 1_000_000_000),
                "ts_closed": closed.isoformat(),
                "realized_pnl": "1000 USDT",
            }
        )


def make_block(root: Path, *, block: str, direction: str, day: int) -> None:
    block_root = root / block
    block_root.mkdir(parents=True)
    opened = datetime(2025, 1, day, 12, tzinfo=UTC)
    observed_ns = int(opened.timestamp() * 1_000_000_000)
    scenario_id = f"{block}-{direction}"
    write_position(
        block_root / "positions.csv",
        symbol="BTCUSDT",
        direction=direction,
        opened=opened,
    )
    write_json(
        block_root / "submitted_plans.json",
        {
            "plans": [
                {
                    "scenario_id": scenario_id,
                    "symbol": "BTCUSDT",
                    "direction": direction,
                    "scenario": "FAR",
                    "nav_before": "100000",
                    "observed_ts_ns": observed_ns,
                    "details": {
                        "pool_source": "ASIA_2000_0000_NY",
                        "sweep_ts_ns": observed_ns - 60_000_000_000,
                    },
                }
            ]
        },
    )
    write_json(
        block_root / "order_lifecycle.json",
        {
            "events": [
                {
                    "type": "GLOBAL_ENTRY_FILLED",
                    "scenario_id": scenario_id,
                    "symbol": "BTCUSDT",
                    "ts_event": observed_ns,
                }
            ]
        },
    )
    daily = pow(1.01, 1 / 28) - 1
    write_json(
        block_root / "metrics.json",
        {
            "starting_nav": "100000",
            "final_nav": "101000",
            "daily_geometric_growth": daily,
            "closed_trades": 1,
            "wins": 1,
            "losses": 0,
            "scenario_max_hold_exit_count": 0,
            "resolution_tail_unresolved_count": 0,
        },
    )
    write_json(block_root / "summary.json", {"block": block})
    write_json(
        block_root / "audit.json",
        {key: True for key in SAFETY_KEYS},
    )


class AggregateTest(unittest.TestCase):
    def test_development_gate_can_pass_without_alpha_claim(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_block(root, block="D1", direction="LONG", day=1)
            make_block(root, block="D2", direction="SHORT", day=2)
            make_block(root, block="D3", direction="LONG", day=3)
            protocol = {
                "candidate": "test-core-far",
                "schema": "test-protocol",
                "selection": {
                    "evaluation_days": 28,
                    "blocks": {
                        "D1": {"start": "2025-01-01", "end_exclusive": "2025-01-29"},
                        "D2": {"start": "2025-02-01", "end_exclusive": "2025-03-01"},
                        "D3": {"start": "2025-03-01", "end_exclusive": "2025-03-29"},
                    },
                },
                "development_gate": {
                    "minimum_blocks": 3,
                    "minimum_economic_clusters": 3,
                    "minimum_pooled_daily_geometric_growth": 0.0,
                    "maximum_positive_log_growth_share_from_one_cluster": 0.34,
                    "claimed_directions": ["LONG", "SHORT"],
                    "minimum_clusters_per_claimed_direction": 1,
                },
                "decision": {
                    "gate_pass": "freeze fresh validation",
                    "gate_fail": "reject",
                },
            }
            result = aggregate(root, protocol)
            self.assertTrue(result["gate_passed"])
            self.assertTrue(result["fresh_validation_authorized"])
            self.assertFalse(result["validation_eligible"])
            self.assertFalse(result["success_claim"])
            self.assertEqual(result["economic_clusters"], 3)
            self.assertEqual(result["closed_trades"], 3)


if __name__ == "__main__":
    unittest.main()
