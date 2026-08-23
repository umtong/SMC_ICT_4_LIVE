from __future__ import annotations

import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from smc_ict_4.episode_policy_live.domain import Bar, EntryZone, LiquidityBoundary
from smc_ict_4.episode_policy_live.inventory_ownership import (
    FIVE_MINUTE_NS,
    InventoryInterpretation,
)
from smc_ict_4.episode_policy_live.live_inventory import (
    GLOBAL_ACCOUNT_RATIO_PATH,
    OPEN_INTEREST_PATH,
    InventoryMetricConflictError,
    LiveBinanceInventoryCollector,
    LiveInventoryStatus,
)
from smc_ict_4.episode_policy_live.live import apply_live_inventory_results
from smc_ict_4.episode_policy_live.policy import EpisodeWatch, SymbolEpisodePolicy


BASE_MS = 1_767_225_600_000
BASE_NS = BASE_MS * 1_000_000


def open_interest_row(
    slot: int,
    *,
    symbol: str = "BTCUSDT",
    oi: str | float = "1000.0",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "sumOpenInterest": oi,
        "sumOpenInterestValue": str(float(oi) * 100.0),
        "timestamp": BASE_MS + slot * 300_000,
    }


def ratio_row(
    slot: int,
    *,
    symbol: str = "BTCUSDT",
    ratio: str | float = "1.0",
    timestamp_offset_ms: int = 0,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "longShortRatio": ratio,
        "longAccount": "0.5",
        "shortAccount": "0.5",
        "timestamp": BASE_MS + slot * 300_000 + timestamp_offset_ms,
    }


class FakeTransport:
    def __init__(self) -> None:
        self.open_interest: dict[str, object] = {}
        self.account_ratio: dict[str, object] = {}
        self.fail_paths: set[str] = set()
        self.calls: list[str] = []

    def __call__(self, url: str, *, timeout_seconds: float) -> object:
        self.calls.append(url)
        self.assert_timeout = timeout_seconds
        parsed = urlparse(url)
        if parsed.path in self.fail_paths:
            raise TimeoutError(f"injected failure for {parsed.path}")
        symbol = parse_qs(parsed.query)["symbol"][0]
        if parsed.path == OPEN_INTEREST_PATH:
            return self.open_interest[symbol]
        if parsed.path == GLOBAL_ACCOUNT_RATIO_PATH:
            return self.account_ratio[symbol]
        raise AssertionError(f"unexpected endpoint: {url}")


def seed(
    transport: FakeTransport,
    *,
    symbol: str = "BTCUSDT",
    last_slot: int = 4,
) -> None:
    first = last_slot - 3
    transport.open_interest[symbol] = [
        open_interest_row(slot, symbol=symbol, oi=1_100 - slot * 20)
        for slot in range(first, last_slot + 1)
    ]
    transport.account_ratio[symbol] = [
        ratio_row(slot, symbol=symbol, ratio=1.2 - slot * 0.02)
        for slot in range(first, last_slot + 1)
    ]


class LiveInventoryCollectorTests(unittest.TestCase):
    def collector(
        self,
        transport: FakeTransport,
        *,
        now_slot: int = 4,
        symbols: tuple[str, ...] = ("BTCUSDT",),
    ) -> LiveBinanceInventoryCollector:
        now_ns = BASE_NS + now_slot * FIVE_MINUTE_NS + 2_000_000_000
        return LiveBinanceInventoryCollector(
            symbols=symbols,
            transport=transport,
            clock_ns=lambda: now_ns,
            completion_lag_ns=1_000_000_000,
        )

    def test_public_exact_join_builds_historical_inventory_semantics(self) -> None:
        transport = FakeTransport()
        seed(transport)
        result = self.collector(transport).poll("BTCUSDT")

        self.assertTrue(result.ready)
        self.assertEqual(result.status, LiveInventoryStatus.READY)
        self.assertEqual(result.added_points, 4)
        assert result.timeline is not None
        self.assertEqual(len(result.timeline.points), 4)
        current = result.timeline.points[-1]
        self.assertEqual(current.nominal_ts_ns, BASE_NS + 4 * FIVE_MINUTE_NS)
        self.assertIsNone(current.top_account_long_short)
        self.assertIsNone(current.top_position_long_short)
        self.assertIsNone(current.taker_buy_sell_ratio)

        interpretation = result.timeline.evaluate(
            shock_side="SELL",
            episode_start_ns=BASE_NS + 90_000_000_000,
            decision_ts_ns=current.observed_ts_ns,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(
            interpretation.interpretation,
            InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE,
        )

    def test_requests_are_bounded_public_market_data_only(self) -> None:
        transport = FakeTransport()
        seed(transport)
        self.collector(transport).poll("BTCUSDT")
        self.assertEqual(len(transport.calls), 2)
        paths = {urlparse(url).path for url in transport.calls}
        self.assertEqual(paths, {OPEN_INTEREST_PATH, GLOBAL_ACCOUNT_RATIO_PATH})
        for url in transport.calls:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "fapi.binance.com")
            self.assertEqual(query, {
                "symbol": ["BTCUSDT"],
                "period": ["5m"],
                "limit": ["8"],
            })
            self.assertNotIn("key", url.lower())
            self.assertNotIn("toplongshort", url.lower())
            self.assertNotIn("takerlongshort", url.lower())

    def test_identical_repoll_is_idempotent(self) -> None:
        transport = FakeTransport()
        seed(transport)
        collector = self.collector(transport)
        first = collector.poll("BTCUSDT")
        second = collector.poll("BTCUSDT")
        self.assertEqual(first.added_points, 4)
        self.assertEqual(second.added_points, 0)
        self.assertEqual(len(collector.history("BTCUSDT").points), 4)

    def test_semantically_equal_numeric_format_is_not_a_conflict(self) -> None:
        transport = FakeTransport()
        seed(transport)
        collector = self.collector(transport)
        collector.poll("BTCUSDT")
        rows = list(transport.open_interest["BTCUSDT"])
        rows[-1] = open_interest_row(4, oi="1020.000000")
        transport.open_interest["BTCUSDT"] = rows
        result = collector.poll("BTCUSDT")
        self.assertTrue(result.ready)
        self.assertEqual(result.added_points, 0)

    def test_conflicting_repeat_in_one_response_is_fatal(self) -> None:
        transport = FakeTransport()
        seed(transport)
        transport.open_interest["BTCUSDT"] = [
            open_interest_row(4, oi="1000"),
            open_interest_row(4, oi="999"),
        ]
        with self.assertRaisesRegex(
            InventoryMetricConflictError,
            "conflicting repeated",
        ):
            self.collector(transport).poll("BTCUSDT")

    def test_provider_revision_of_published_join_is_fatal_and_atomic(self) -> None:
        transport = FakeTransport()
        seed(transport)
        collector = self.collector(transport)
        collector.poll("BTCUSDT")
        rows = list(transport.open_interest["BTCUSDT"])
        rows[-1] = open_interest_row(4, oi="999")
        transport.open_interest["BTCUSDT"] = rows
        with self.assertRaisesRegex(
            InventoryMetricConflictError,
            "conflicting repeated joined",
        ):
            collector.poll("BTCUSDT")
        self.assertEqual(
            collector.history("BTCUSDT").points[-1].open_interest,
            1020.0,
        )

    def test_endpoint_failure_is_explicit_unknown_and_never_reuses_stale_timeline(self) -> None:
        transport = FakeTransport()
        seed(transport)
        collector = self.collector(transport)
        self.assertTrue(collector.poll("BTCUSDT").ready)
        transport.fail_paths.add(OPEN_INTEREST_PATH)
        failed = collector.poll("BTCUSDT")
        self.assertEqual(failed.status, LiveInventoryStatus.ENDPOINT_UNAVAILABLE)
        self.assertFalse(failed.ready)
        self.assertIsNone(failed.timeline)
        assert failed.gap is not None
        self.assertEqual(failed.gap.reason, "PUBLIC_METRICS_ENDPOINT_UNAVAILABLE")
        self.assertIn(OPEN_INTEREST_PATH, failed.gap.endpoint_errors[0])
        # Retention is diagnostic only; readiness never falls back to it.
        self.assertEqual(len(collector.history("BTCUSDT").points), 4)
        self.assertIs(collector.last_result("BTCUSDT"), failed)

    def test_ready_result_expires_at_the_next_completed_five_minute_slot(self) -> None:
        transport = FakeTransport()
        seed(transport)
        clock = [BASE_NS + 4 * FIVE_MINUTE_NS + 2_000_000_000]
        collector = LiveBinanceInventoryCollector(
            symbols=("BTCUSDT",),
            transport=transport,
            clock_ns=lambda: clock[0],
            completion_lag_ns=1_000_000_000,
        )
        ready = collector.poll("BTCUSDT")
        self.assertIs(collector.current("BTCUSDT"), ready)

        clock[0] += FIVE_MINUTE_NS
        stale = collector.current("BTCUSDT")
        self.assertEqual(stale.status, LiveInventoryStatus.STALE)
        self.assertIsNone(stale.timeline)

    def test_latest_join_older_than_expected_slot_is_explicit_stale(self) -> None:
        transport = FakeTransport()
        seed(transport, last_slot=3)
        result = self.collector(transport, now_slot=4).poll("BTCUSDT")
        self.assertEqual(result.status, LiveInventoryStatus.STALE)
        self.assertIsNone(result.timeline)
        self.assertEqual(result.latest_joined_nominal_ts_ns, BASE_NS + 3 * FIVE_MINUTE_NS)
        assert result.gap is not None
        self.assertEqual(result.gap.reason, "LATEST_PUBLIC_METRICS_SNAPSHOT_IS_STALE")

    def test_current_rows_with_different_raw_timestamps_are_not_nearest_joined(self) -> None:
        transport = FakeTransport()
        seed(transport)
        ratios = list(transport.account_ratio["BTCUSDT"])
        ratios[-1] = ratio_row(4, ratio=1.12, timestamp_offset_ms=-1)
        transport.account_ratio["BTCUSDT"] = ratios
        result = self.collector(transport).poll("BTCUSDT")
        self.assertEqual(result.status, LiveInventoryStatus.NO_EXACT_JOIN)
        self.assertIsNone(result.timeline)
        assert result.gap is not None
        self.assertEqual(
            result.gap.reason,
            "CURRENT_PUBLIC_METRICS_TIMESTAMP_NOT_EXACTLY_JOINED",
        )

    def test_future_rows_are_not_published_as_completed(self) -> None:
        transport = FakeTransport()
        transport.open_interest["BTCUSDT"] = [open_interest_row(5)]
        transport.account_ratio["BTCUSDT"] = [ratio_row(5)]
        result = self.collector(transport, now_slot=4).poll("BTCUSDT")
        self.assertEqual(result.status, LiveInventoryStatus.NO_COMPLETED_SNAPSHOT)
        self.assertIsNone(result.timeline)

    def test_schema_error_is_non_ready_not_process_failure(self) -> None:
        transport = FakeTransport()
        seed(transport)
        transport.account_ratio["BTCUSDT"] = {"code": -1000, "msg": "bad"}
        result = self.collector(transport).poll("BTCUSDT")
        self.assertEqual(result.status, LiveInventoryStatus.INVALID_RESPONSE)
        self.assertIsNone(result.timeline)
        assert result.gap is not None
        self.assertIn("response must be a JSON array", result.gap.endpoint_errors[0])

    def test_poll_all_keeps_symbols_separate(self) -> None:
        transport = FakeTransport()
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
            seed(transport, symbol=symbol)
        collector = self.collector(
            transport,
            symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
        )
        results = collector.poll_all()
        self.assertEqual({item.symbol for item in results}, set(collector.symbols))
        self.assertTrue(all(item.ready for item in results))
        self.assertEqual(len(transport.calls), 8)

    def test_live_handoff_matches_replay_timeline_and_proposal_identity(self) -> None:
        transport = FakeTransport()
        symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        for symbol in symbols:
            seed(transport, symbol=symbol)
        results = self.collector(transport, symbols=symbols).poll_all()

        live_policies = {
            symbol: SymbolEpisodePolicy(symbol, 0.1)
            for symbol in symbols
        }
        replay_policies = {
            symbol: SymbolEpisodePolicy(symbol, 0.1)
            for symbol in symbols
        }
        strategy = SimpleNamespace(
            coordinator=SimpleNamespace(policies=live_policies),
        )
        statuses = apply_live_inventory_results(strategy, results)
        for result in results:
            replay_policies[result.symbol].inventory_timeline = result.timeline
        self.assertEqual(set(statuses.values()), {"READY"})

        live = live_policies["BTCUSDT"]
        replay = replay_policies["BTCUSDT"]
        assert live.inventory_timeline is not None
        assert replay.inventory_timeline is not None
        live_decision = live.inventory_timeline.evaluate(
            shock_side="SELL",
            episode_start_ns=BASE_NS + FIVE_MINUTE_NS,
            decision_ts_ns=BASE_NS + 4 * FIVE_MINUTE_NS,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        replay_decision = replay.inventory_timeline.evaluate(
            shock_side="SELL",
            episode_start_ns=BASE_NS + FIVE_MINUTE_NS,
            decision_ts_ns=BASE_NS + 4 * FIVE_MINUTE_NS,
            price_move=-1.0,
            signed_taker_flow=-0.1,
        )
        self.assertEqual(live_decision, replay_decision)

        def proposal(policy: SymbolEpisodePolicy):
            decision_bar = Bar(
                symbol="BTCUSDT",
                interval_minutes=1,
                open_time_ns=BASE_NS + 4 * FIVE_MINUTE_NS - 60_000_000_000,
                close_time_ns=BASE_NS + 4 * FIVE_MINUTE_NS,
                open=101.0,
                high=102.2,
                low=100.8,
                close=102.0,
                volume=100.0,
                quote_volume=10_200.0,
                taker_buy_quote_volume=4_000.0,
                trade_count=100,
            )
            source = LiquidityBoundary(
                boundary_id="SOURCE:PARITY",
                symbol="BTCUSDT",
                side="LOW",
                kind="SWING_60M",
                timeframe_minutes=60,
                observed_time_ns=BASE_NS,
                lower=98.9,
                upper=99.1,
                price=99.0,
                strength=3.0,
            )
            destination = LiquidityBoundary(
                boundary_id="DEST:PARITY",
                symbol="BTCUSDT",
                side="HIGH",
                kind="SWING_15M",
                timeframe_minutes=15,
                observed_time_ns=BASE_NS,
                lower=109.8,
                upper=110.2,
                price=110.0,
                strength=3.0,
            )
            policy.market.serial_5m = 20
            policy.market.five_minute.append(decision_bar)
            policy.market.boundary_book.boundaries[destination.boundary_id] = destination
            watch = EpisodeWatch(
                episode_id="EP:PARITY",
                family="FAILED_AUCTION_REVERSAL",
                source=source,
                side="LONG",
                state="FAILED_AUCTION_RECLAIM_COMPLETED",
                interaction_serial=19,
                interaction_time_ns=BASE_NS + FIVE_MINUTE_NS,
                event_extreme=98.0,
                last_update_serial=20,
                last_update_time_ns=decision_bar.close_time_ns,
                ownership_balance=0.1,
                evidence={
                    "inventory_interpretation": live_decision.interpretation.value,
                    "inventory_reason": live_decision.reason,
                },
            )
            zone = EntryZone(
                "SOURCE_ORDER_BLOCK",
                99.5,
                100.0,
                BASE_NS,
                decision_bar.open_time_ns,
            )
            plan = policy._build_plan(
                watch,
                decision_bar,
                20,
                1.0,
                {"event_residual_ownership": 0.1},
                zone,
            )
            assert plan is not None
            return policy._refresh_proposals([plan], decision_bar)[0]

        live_plan = proposal(live)
        replay_plan = proposal(replay)
        self.assertEqual(live_plan.plan_id, replay_plan.plan_id)
        self.assertEqual(live_plan.episode_id, replay_plan.episode_id)

    def test_non_ready_handoff_clears_every_previously_ready_policy(self) -> None:
        transport = FakeTransport()
        symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        for symbol in symbols:
            seed(transport, symbol=symbol)
        clock = [BASE_NS + 4 * FIVE_MINUTE_NS + 2_000_000_000]
        collector = LiveBinanceInventoryCollector(
            symbols=symbols,
            transport=transport,
            clock_ns=lambda: clock[0],
            completion_lag_ns=1_000_000_000,
        )
        policies = {
            symbol: SimpleNamespace(inventory_timeline=None)
            for symbol in symbols
        }
        strategy = SimpleNamespace(
            coordinator=SimpleNamespace(policies=policies),
        )
        apply_live_inventory_results(strategy, collector.poll_all())
        self.assertTrue(all(policy.inventory_timeline is not None for policy in policies.values()))

        clock[0] += FIVE_MINUTE_NS
        apply_live_inventory_results(
            strategy,
            tuple(collector.current(symbol) for symbol in symbols),
        )
        self.assertTrue(all(policy.inventory_timeline is None for policy in policies.values()))

    def test_limit_must_seed_the_three_change_window_and_remain_bounded(self) -> None:
        transport = FakeTransport()
        with self.assertRaisesRegex(ValueError, "between 4 and 30"):
            LiveBinanceInventoryCollector(
                symbols=("BTCUSDT",),
                transport=transport,
                limit=3,
            )
        with self.assertRaisesRegex(ValueError, "between 4 and 30"):
            LiveBinanceInventoryCollector(
                symbols=("BTCUSDT",),
                transport=transport,
                limit=31,
            )


if __name__ == "__main__":
    unittest.main()
