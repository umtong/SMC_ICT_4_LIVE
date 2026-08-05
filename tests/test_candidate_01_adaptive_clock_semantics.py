from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import sys
from statistics import median
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
SRC = ROOT / "src"
for item in (CANDIDATE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from adaptive_aggtrade_clock import evidence_for_record
from aggtrade_clock import (
    calibrate_target_from_minutes,
    iter_volume_bars,
    minute_quote_totals,
)
from aggtrade_data import AggTradeDownload, iter_download


NS_PER_MINUTE = 60_000_000_000


class AdaptiveClockSemanticTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        day = date(2023, 1, 1)
        archive = root / "BTCUSDT-aggTrades-2023-01-01.zip"
        start = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        rows: list[str] = []
        for index in range(180):
            minute = index // 3
            price = 100.0 + (minute % 12) * 0.2 + (index % 3) * 0.05
            quantity = 5.0 + (index % 5)
            timestamp = start + minute * NS_PER_MINUTE + (index % 3) * 1_000_000_000
            buyer_maker = "true" if index % 2 else "false"
            rows.append(
                f"{index},{price},{quantity},{index},{index},{timestamp},{buyer_maker}\n",
            )
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
            output.writestr("BTCUSDT-aggTrades-2023-01-01.csv", "".join(rows))
        self.record = AggTradeDownload(
            symbol="BTCUSDT",
            day=day.isoformat(),
            url="https://example.invalid/archive.zip",
            checksum_url="https://example.invalid/archive.zip.CHECKSUM",
            path=str(archive),
            checksum_path=str(root / "dummy.CHECKSUM"),
            size_bytes=archive.stat().st_size,
            sha256="0" * 64,
            expected_sha256="0" * 64,
        )
        self.start_ns = start
        self.end_ns = start + 24 * 60 * NS_PER_MINUTE

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_concurrent_candidate_scan_matches_reference_bar_builder(self) -> None:
        minutes = (5, 10, 20)
        totals = minute_quote_totals(
            iter_download(self.record),
            start_ns=self.start_ns,
            end_ns=self.end_ns,
        )
        observed = {
            item.calibration_minutes: item
            for item in evidence_for_record(
                self.record,
                candidate_minutes=minutes,
            )
        }
        for value in minutes:
            target = calibrate_target_from_minutes(
                totals,
                minutes_per_event=value,
            )
            reference = list(
                iter_volume_bars(
                    iter_download(self.record),
                    target_quote_notional=target,
                    include_partial=False,
                ),
            )
            self.assertGreater(len(reference), 0)
            item = observed[value]
            self.assertAlmostEqual(item.target_quote_notional, target)
            self.assertEqual(item.calibration_event_bars, len(reference))
            self.assertAlmostEqual(
                item.median_range_bps,
                median(bar.range_fraction * 10_000.0 for bar in reference),
            )
            self.assertAlmostEqual(
                item.median_duration_seconds,
                median(bar.duration_seconds for bar in reference),
            )


if __name__ == "__main__":
    unittest.main()
