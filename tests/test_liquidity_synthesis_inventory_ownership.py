from __future__ import annotations

import csv
from dataclasses import replace
from datetime import date
from hashlib import sha256
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch as mock_patch
from urllib.error import HTTPError
import zipfile

from smc_ict_4.episode_policy_live.inventory_ownership import (
    CONFLICTING_OFFICIAL_DUPLICATE,
    FIVE_MINUTE_NS,
    MINUTE_NS,
    InventoryInterpretation,
    InventoryMetric,
    InventoryRegime,
    InventoryTimeline,
    OwnershipBranch,
    causal_metric_clock,
    classify_inventory_change,
    download_official_metrics_range,
    load_official_metrics_archives,
)


def metric(
    slot: int,
    *,
    oi: float | None,
    all_ratio: float | None,
    symbol: str = "BTCUSDT",
    delay_seconds: int = 0,
    invalid_fields: tuple[str, ...] = (),
    top_account: float | None = 1.1,
) -> InventoryMetric:
    source = slot + delay_seconds * 1_000_000_000
    nominal, observed = causal_metric_clock(source)
    return InventoryMetric(
        symbol=symbol,
        source_ts_ns=source,
        nominal_ts_ns=nominal,
        observed_ts_ns=observed,
        open_interest=oi,
        open_interest_value=None if oi is None else oi * 100.0,
        all_account_long_short=all_ratio,
        top_account_long_short=top_account,
        top_position_long_short=1.2,
        taker_buy_sell_ratio=0.8,
        invalid_fields=invalid_fields,
    )


class InventoryOwnershipTests(unittest.TestCase):
    @staticmethod
    def _zip_payload() -> bytes:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("metrics.csv", "create_time,symbol,sum_open_interest\n")
        return payload.getvalue()

    def test_source_timestamp_is_never_shifted_earlier(self) -> None:
        nominal = 1_700_000_100_000_000_000
        nominal -= nominal % FIVE_MINUTE_NS
        source = nominal + 17_000_000_000
        actual_nominal, observed = causal_metric_clock(source)
        self.assertEqual(actual_nominal, nominal)
        self.assertGreaterEqual(observed, source)
        self.assertEqual(observed % MINUTE_NS, 0)

    def test_identical_duplicate_is_collapsed_with_evidence(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        point = metric(start, oi=1_000.0, all_ratio=1.0)
        first = replace(point, source_archive="day-1.zip", source_archive_sha256="a" * 64)
        second = replace(point, source_archive="day-2.zip", source_archive_sha256="b" * 64)
        timeline = InventoryTimeline([first, second])
        self.assertEqual(timeline.points, (first,))
        self.assertEqual(timeline.duplicate_observed_ts_ns, (point.observed_ts_ns,))

    def test_conflicting_duplicate_merges_to_unknown_with_both_sources(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        first = replace(
            metric(start, oi=1_000.0, all_ratio=1.0),
            source_fingerprint="first",
            source_archive="day-1.zip",
            source_archive_sha256="a" * 64,
        )
        conflicting = replace(
            metric(start, oi=999.0, all_ratio=1.0),
            source_fingerprint="second",
            source_archive="day-2.zip",
            source_archive_sha256="b" * 64,
        )
        timeline = InventoryTimeline([first, conflicting])
        merged = timeline.points[0]
        self.assertIsNone(merged.open_interest)
        self.assertIn(CONFLICTING_OFFICIAL_DUPLICATE, merged.invalid_fields)
        self.assertEqual(len(timeline.conflicting_duplicates), 1)
        evidence = timeline.conflicting_duplicates[0]
        self.assertEqual(evidence.source_archives, ("day-1.zip", "day-2.zip"))
        self.assertEqual(evidence.source_archive_sha256, ("a" * 64, "b" * 64))

    def test_contraction_discharge_is_forced_position_reset(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        prior = metric(start, oi=1_000.0, all_ratio=1.2)
        current = metric(start + 15 * MINUTE_NS, oi=920.0, all_ratio=1.0)
        decision = classify_inventory_change(
            prior,
            current,
            shock_side="SELL",
            episode_start_ns=start + 10 * MINUTE_NS,
            decision_ts_ns=current.observed_ts_ns,
            price_move=-2.0,
            signed_taker_flow=-0.2,
        )
        self.assertEqual(decision.regime, InventoryRegime.POSITION_RESET)
        self.assertEqual(decision.ownership, OwnershipBranch.ALIGNED_COMPOSITION)
        self.assertEqual(
            decision.interpretation,
            InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE,
        )
        self.assertAlmostEqual(decision.oi_change_fraction or 0.0, -0.08)

    def test_contraction_counter_inventory_is_not_called_discharge(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        prior = metric(start, oi=1_000.0, all_ratio=1.0)
        current = metric(start + 5 * MINUTE_NS, oi=900.0, all_ratio=1.2)
        decision = classify_inventory_change(
            prior,
            current,
            shock_side="SELL",
            episode_start_ns=start + MINUTE_NS,
            decision_ts_ns=current.observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(decision.ownership, OwnershipBranch.COUNTER_INVENTORY)
        self.assertEqual(
            decision.interpretation,
            InventoryInterpretation.FORCED_DELEVERAGING_COUNTER_INVENTORY,
        )

    def test_expansion_is_fresh_sponsorship_not_position_reset(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        prior = metric(start, oi=1_000.0, all_ratio=1.0)
        current = metric(start + 5 * MINUTE_NS, oi=1_040.0, all_ratio=1.1)
        decision = classify_inventory_change(
            prior,
            current,
            shock_side="BUY",
            episode_start_ns=start + MINUTE_NS,
            decision_ts_ns=current.observed_ts_ns,
            price_move=1.0,
            signed_taker_flow=0.1,
        )
        self.assertEqual(decision.regime, InventoryRegime.FRESH_SPONSORSHIP)
        self.assertEqual(
            decision.interpretation,
            InventoryInterpretation.FRESH_SPONSORSHIP_CROWDING,
        )

    def test_missing_composition_and_gaps_stay_unknown(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        no_composition = classify_inventory_change(
            metric(start, oi=1_000.0, all_ratio=None),
            metric(start + 5 * MINUTE_NS, oi=900.0, all_ratio=None),
            shock_side="SELL",
            episode_start_ns=start + MINUTE_NS,
            decision_ts_ns=start + 5 * MINUTE_NS,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(no_composition.regime, InventoryRegime.POSITION_RESET)
        self.assertFalse(no_composition.known)

        points = [
            metric(start, oi=1_000.0, all_ratio=1.0),
            metric(start + 5 * MINUTE_NS, oi=990.0, all_ratio=0.99),
            # Missing the 10-minute nominal observation.
            metric(start + 15 * MINUTE_NS, oi=970.0, all_ratio=0.97),
            metric(start + 20 * MINUTE_NS, oi=950.0, all_ratio=0.95),
            metric(start + 25 * MINUTE_NS, oi=940.0, all_ratio=0.94),
            metric(start + 30 * MINUTE_NS, oi=930.0, all_ratio=0.93),
        ]
        decision = InventoryTimeline(points).evaluate(
            shock_side="SELL",
            episode_start_ns=start + 12 * MINUTE_NS,
            decision_ts_ns=points[3].observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
            change_points=3,
        )
        self.assertEqual(decision.interpretation, InventoryInterpretation.UNKNOWN)
        self.assertEqual(decision.reason, "METRICS_WINDOW_HAS_GAP")
        resumed = InventoryTimeline(points).evaluate(
            shock_side="SELL",
            episode_start_ns=start + 12 * MINUTE_NS,
            decision_ts_ns=points[-1].observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
            change_points=3,
        )
        self.assertEqual(resumed.regime, InventoryRegime.POSITION_RESET)

    def test_timeline_requires_post_episode_visible_metric(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        points = [
            metric(start + index * FIVE_MINUTE_NS, oi=1_000.0 - index, all_ratio=1.0)
            for index in range(4)
        ]
        timeline = InventoryTimeline(points)
        decision = timeline.evaluate(
            shock_side="SELL",
            episode_start_ns=start + 16 * MINUTE_NS,
            decision_ts_ns=points[-1].observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(decision.reason, "NO_POST_EPISODE_METRIC")

    def test_invalid_official_row_is_unknown_until_valid_comparison_window_resumes(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        points = [
            metric(
                start + index * FIVE_MINUTE_NS,
                oi=None if index == 3 else 1_000.0 - index * 10.0,
                all_ratio=1.2 - index * 0.02,
                invalid_fields=("open_interest",) if index == 3 else (),
            )
            for index in range(8)
        ]
        timeline = InventoryTimeline(points)
        for current_index in (3, 5):
            decision = timeline.evaluate(
                shock_side="SELL",
                episode_start_ns=start + MINUTE_NS,
                decision_ts_ns=points[current_index].observed_ts_ns,
                price_move=-1.0,
                signed_taker_flow=-0.1,
            )
            self.assertEqual(decision.regime, InventoryRegime.UNKNOWN)
            self.assertEqual(
                decision.reason,
                "METRICS_WINDOW_HAS_INVALID_OPEN_INTEREST",
            )
        resumed = timeline.evaluate(
            shock_side="SELL",
            episode_start_ns=start + MINUTE_NS,
            decision_ts_ns=points[7].observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(resumed.regime, InventoryRegime.POSITION_RESET)
        self.assertEqual(
            resumed.interpretation,
            InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE,
        )

    def test_conflicting_duplicate_is_unknown_then_clean_window_resumes(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        points = [
            metric(
                start + index * FIVE_MINUTE_NS,
                oi=1_000.0 - index * 10.0,
                all_ratio=1.2 - index * 0.02,
            )
            for index in range(8)
        ]
        conflict = replace(
            points[3],
            open_interest=points[3].open_interest + 1.0,  # type: ignore[operator]
            source_fingerprint="conflict",
        )
        timeline = InventoryTimeline([*points, conflict])
        blocked = timeline.evaluate(
            shock_side="SELL",
            episode_start_ns=start + MINUTE_NS,
            decision_ts_ns=points[5].observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(blocked.regime, InventoryRegime.UNKNOWN)
        self.assertEqual(
            blocked.reason,
            "METRICS_WINDOW_HAS_CONFLICTING_OFFICIAL_DUPLICATE",
        )
        resumed = timeline.evaluate(
            shock_side="SELL",
            episode_start_ns=start + MINUTE_NS,
            decision_ts_ns=points[7].observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(resumed.regime, InventoryRegime.POSITION_RESET)

    def test_unused_auxiliary_invalidity_does_not_erase_oi_regime(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        points = [
            metric(
                start + index * FIVE_MINUTE_NS,
                oi=1_000.0 - index * 10.0,
                all_ratio=1.2 - index * 0.02,
                top_account=None if index == 3 else 1.1,
                invalid_fields=("top_account_long_short",) if index == 3 else (),
            )
            for index in range(4)
        ]
        decision = InventoryTimeline(points).evaluate(
            shock_side="SELL",
            episode_start_ns=start + MINUTE_NS,
            decision_ts_ns=points[-1].observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(decision.regime, InventoryRegime.POSITION_RESET)
        self.assertEqual(
            decision.interpretation,
            InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE,
        )

    def test_invalid_all_account_keeps_regime_but_not_ownership(self) -> None:
        start = 1_700_000_100_000_000_000
        start -= start % FIVE_MINUTE_NS
        prior = metric(start, oi=1_000.0, all_ratio=1.1)
        current = metric(
            start + FIVE_MINUTE_NS,
            oi=950.0,
            all_ratio=None,
            invalid_fields=("all_account_long_short",),
        )
        decision = classify_inventory_change(
            prior,
            current,
            shock_side="SELL",
            episode_start_ns=start + MINUTE_NS,
            decision_ts_ns=current.observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(decision.regime, InventoryRegime.POSITION_RESET)
        self.assertEqual(decision.ownership, OwnershipBranch.UNKNOWN)
        self.assertEqual(decision.interpretation, InventoryInterpretation.UNKNOWN)

    def test_checksum_verified_official_archive_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "BTCUSDT-metrics-2026-01-01.zip"
            header = [
                "create_time",
                "symbol",
                "sum_open_interest",
                "sum_open_interest_value",
                "count_toptrader_long_short_ratio",
                "sum_toptrader_long_short_ratio",
                "count_long_short_ratio",
                "sum_taker_long_short_vol_ratio",
            ]
            base = 1_767_225_600_000
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(header)
            for index in range(4):
                writer.writerow(
                    [
                        base + index * 300_000,
                        "BTCUSDT",
                        0 if index == 1 else 1_000 - index * 10,
                        100_000,
                        1.1,
                        1.2,
                        1.0 - index * 0.01,
                        0.8,
                    ],
                )
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("BTCUSDT-metrics-2026-01-01.csv", buffer.getvalue())
            digest = sha256(archive.read_bytes()).hexdigest()
            Path(str(archive) + ".CHECKSUM").write_text(
                f"{digest}  {archive.name}\n",
                encoding="utf-8",
            )
            timeline = load_official_metrics_archives("BTCUSDT", [archive])
            self.assertEqual(len(timeline.points), 4)
            self.assertEqual(timeline.points[0].symbol, "BTCUSDT")
            self.assertIsNone(timeline.points[1].open_interest)
            self.assertEqual(timeline.points[1].invalid_fields, ("open_interest",))

    def test_bounded_downloader_uses_half_open_range_and_reuses_verified_files(self) -> None:
        payload = self._zip_payload()
        digest = sha256(payload).hexdigest()
        calls: list[str] = []

        def request(url: str, *, attempts: int = 4) -> bytes:
            del attempts
            calls.append(url)
            filename = url.rsplit("/", 1)[-1].removesuffix(".CHECKSUM")
            if url.endswith(".CHECKSUM"):
                return f"{digest}  {filename}\n".encode()
            return payload

        with tempfile.TemporaryDirectory() as temporary:
            with mock_patch(
                "smc_ict_4.episode_policy_live.inventory_ownership._request_bytes",
                side_effect=request,
            ):
                result = download_official_metrics_range(
                    symbols=("BTCUSDT", "ETHUSDT"),
                    start=date(2026, 1, 1),
                    end_exclusive=date(2026, 1, 3),
                    destination=temporary,
                    max_workers=2,
                )
            self.assertEqual(len(result.evidence), 4)
            self.assertEqual(result.downloaded, 4)
            self.assertEqual({item.day for item in result.evidence}, {
                date(2026, 1, 1), date(2026, 1, 2),
            })
            self.assertEqual(len(calls), 8)
            self.assertFalse(any(Path(temporary).rglob("*.part")))

            with mock_patch(
                "smc_ict_4.episode_policy_live.inventory_ownership._request_bytes",
                side_effect=AssertionError("verified files must not use the network"),
            ):
                reused = download_official_metrics_range(
                    symbols=("BTCUSDT", "ETHUSDT"),
                    start=date(2026, 1, 1),
                    end_exclusive=date(2026, 1, 3),
                    destination=temporary,
                    max_workers=1,
                )
            self.assertEqual(reused.reused, 4)
            self.assertEqual(reused.verified_bytes, len(payload) * 4)

    def test_downloader_records_404_as_explicit_unavailable(self) -> None:
        def missing(url: str, *, attempts: int = 4) -> bytes:
            del attempts
            raise HTTPError(url, 404, "not found", None, None)

        with tempfile.TemporaryDirectory() as temporary:
            with mock_patch(
                "smc_ict_4.episode_policy_live.inventory_ownership._request_bytes",
                side_effect=missing,
            ):
                result = download_official_metrics_range(
                    symbols=("SOLUSDT",),
                    start=date(2020, 1, 1),
                    end_exclusive=date(2020, 1, 2),
                    destination=temporary,
                    max_workers=1,
                )
        self.assertEqual(result.unavailable, 1)
        self.assertEqual(
            result.evidence[0].status,
            "OFFICIAL_ARCHIVE_UNAVAILABLE",
        )
        self.assertIsNone(result.evidence[0].archive_path)


if __name__ == "__main__":
    unittest.main()
