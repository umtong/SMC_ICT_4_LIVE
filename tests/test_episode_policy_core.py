from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from smc_ict_4.episode_policy_live.domain import (
    Bar,
    DEFAULT_CONTRACTS,
    EntryZone,
    LiquidityBoundary,
)
from smc_ict_4.episode_policy_live.live import MinuteTradeBuilder
from smc_ict_4.episode_policy_live.market_state import BarAggregator, PivotTracker
from smc_ict_4.episode_policy_live.policy import (
    EpisodeWatch,
    LiquidityEpisodeCoordinator,
    PolicyConfig,
    SymbolEpisodePolicy,
)
from smc_ict_4.episode_policy_live.storage import StateStore


MIN = 60_000_000_000


def bar(
    symbol="BTCUSDT",
    minute=0,
    interval=1,
    open_=100.0,
    high=101.0,
    low=99.0,
    close=100.5,
    quote=1_000_000.0,
    buy_quote=550_000.0,
):
    return Bar(
        symbol=symbol,
        interval_minutes=interval,
        open_time_ns=minute * MIN,
        close_time_ns=(minute + interval) * MIN - 1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=quote / max(close, 1e-12),
        quote_volume=quote,
        taker_buy_quote_volume=buy_quote,
        trade_count=100,
    )


class AggregationTests(unittest.TestCase):
    def test_five_minute_aggregation_is_close_time_causal(self):
        aggregate = BarAggregator("BTCUSDT", 1, 5)
        output = None
        for index in range(5):
            output = aggregate.push(
                bar(minute=index, open_=100 + index, high=101 + index, low=99 + index, close=100.5 + index)
            )
        self.assertIsNotNone(output)
        self.assertEqual(output.interval_minutes, 5)
        self.assertEqual(output.open, 100.0)
        self.assertEqual(output.close, 104.5)
        self.assertEqual(output.close_time_ns, 5 * MIN - 1)

    def test_pivot_is_observed_after_right_span(self):
        tracker = PivotTracker("BTCUSDT", 15, 2)
        highs = [100, 102, 110, 103, 101]
        emitted = []
        for index, high in enumerate(highs):
            emitted.extend(
                tracker.push(
                    bar(
                        minute=index * 15,
                        interval=15,
                        open_=high - 2,
                        high=high,
                        low=high - 4,
                        close=high - 1,
                    )
                )
            )
        high_pivots = [item for item in emitted if item.side == "HIGH"]
        self.assertEqual(len(high_pivots), 1)
        pivot = high_pivots[0]
        self.assertEqual(pivot.event_time_ns, 45 * MIN - 1)
        self.assertEqual(pivot.observed_time_ns, 75 * MIN - 1)
        self.assertGreater(pivot.observed_time_ns, pivot.event_time_ns)


class PolicyGeometryTests(unittest.TestCase):
    def test_target_must_preexist_and_rr_is_output(self):
        policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
        for index in range(100):
            policy.market.five_minute.append(
                bar(minute=index * 5, interval=5, open_=100, high=102, low=99, close=101)
            )
        policy.market.serial_5m = 99
        # The plan is known only at this close.  Keep the first-return entry
        # below the completed decision bar so the test does not revive a price
        # which already traded before the decision existed.
        decision = bar(
            minute=99 * 5,
            interval=5,
            open_=101.0,
            high=102.0,
            low=100.5,
            close=101.0,
        )
        policy.market.five_minute[-1] = decision
        source = LiquidityBoundary(
            boundary_id="SRC",
            symbol="BTCUSDT",
            side="LOW",
            kind="SWING_15M",
            timeframe_minutes=15,
            observed_time_ns=decision.close_time_ns - 60 * MIN,
            lower=98.9,
            upper=99.1,
            price=99.0,
            strength=2.0,
            anchor_serial=99,
        )
        destination = LiquidityBoundary(
            boundary_id="DST",
            symbol="BTCUSDT",
            side="HIGH",
            kind="HORIZONTAL_OBJECTIVE_15M",
            timeframe_minutes=15,
            observed_time_ns=decision.close_time_ns - 120 * MIN,
            lower=105.1,
            upper=105.1,
            price=105.1,
            strength=3.0,
            anchor_serial=99,
        )
        policy.market.objective_book.register(
            destination,
            source_boundary_id="",
        )
        watch = EpisodeWatch(
            episode_id="EP",
            family="FAILED_AUCTION_REVERSAL",
            source=source,
            side="LONG",
            state="RECLAIMED",
            interaction_serial=98,
            interaction_time_ns=decision.close_time_ns - 5 * MIN,
            event_extreme=98.0,
            last_update_serial=98,
            last_update_time_ns=decision.close_time_ns - 5 * MIN,
            bars_remaining=3,
        )
        zone = EntryZone("SOURCE_BOUNDARY_RETEST", 99.0, 100.0, source.observed_time_ns, decision.open_time_ns)
        plan = policy._build_plan(
            watch,
            decision,
            99,
            1.0,
            {"control_score": 1.0, "common_breadth_signed": 0.5},
            zone,
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.destination_boundary_id, "DST")
        self.assertGreaterEqual(plan.gross_rr, 1.0)
        self.assertLessEqual(destination.observed_time_ns, plan.decision_time_ns)

        future = LiquidityBoundary(
            boundary_id="FUTURE",
            symbol="BTCUSDT",
            side="HIGH",
            kind="HORIZONTAL_OBJECTIVE_5M",
            timeframe_minutes=5,
            observed_time_ns=decision.close_time_ns + MIN,
            lower=103.1,
            upper=103.1,
            price=103.1,
            strength=10.0,
            anchor_serial=99,
        )
        policy.market.objective_book = type(policy.market.objective_book)(
            "BTCUSDT",
            0.1,
        )
        policy.market.objective_book.register(future, source_boundary_id="")
        self.assertIsNone(
            policy._build_plan(
                watch,
                decision,
                99,
                1.0,
                {"control_score": 1.0, "common_breadth_signed": 0.5},
                zone,
            )
        )




class ContinuityTests(unittest.TestCase):
    def test_four_market_coordinator_is_deterministic_and_clock_synchronized(self):
        policies = {
            symbol: SymbolEpisodePolicy(
                symbol,
                float(DEFAULT_CONTRACTS[symbol].tick_size),
                PolicyConfig(min_history_5m=10_000),
            )
            for symbol in DEFAULT_CONTRACTS
        }
        coordinator = LiquidityEpisodeCoordinator(policies)
        for minute in range(5):
            emitted = []
            for symbol in reversed(tuple(DEFAULT_CONTRACTS)):
                emitted.extend(
                    coordinator.push_bar(
                        bar(
                            symbol=symbol,
                            minute=minute,
                            open_=100.0 + minute,
                            high=101.0 + minute,
                            low=99.0 + minute,
                            close=100.5 + minute,
                        )
                    )
                )
            self.assertEqual(emitted, [])
        for policy in policies.values():
            self.assertEqual(len(policy.market.five_minute), 1)
            self.assertEqual(policy.market.five_minute[-1].close_time_ns, 5 * MIN - 1)

class StorageTests(unittest.TestCase):
    def test_idempotency_mutation_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            original = bar(minute=1)
            with StateStore(path) as store:
                self.assertTrue(store.append_bar(original))
                self.assertFalse(store.append_bar(original))
                store.append_event(time_ns=1, event_type="A", payload={"value": 1})
                store.save_snapshot("runtime", time_ns=2, payload={"nav": 100})
                self.assertEqual(store.integrity_check(), "ok")
                self.assertTrue(store.verify_hash_chain())
            with StateStore(path) as store:
                self.assertEqual(store.load_snapshot("runtime")["nav"], 100)
                self.assertEqual(len(store.load_bars(interval_minutes=1)), 1)
                mutated = bar(minute=1, close=101.0)
                with self.assertRaises(RuntimeError):
                    store.append_bar(mutated)


class TradeBuilderTests(unittest.TestCase):
    def test_trade_ticks_create_signed_flow_bar(self):
        builder = MinuteTradeBuilder("BTCUSDT")
        self.assertIsNone(builder.push(ts_ns=1, price=100.0, quantity=1.0, buyer_aggressor=True))
        self.assertIsNone(builder.push(ts_ns=30 * 1_000_000_000, price=101.0, quantity=2.0, buyer_aggressor=False))
        completed = builder.push(ts_ns=MIN + 1, price=102.0, quantity=1.0, buyer_aggressor=True)
        self.assertIsNotNone(completed)
        self.assertEqual(completed.trade_count, 2)
        self.assertEqual(completed.taker_buy_quote_volume, 100.0)
        self.assertEqual(completed.quote_volume, 302.0)


if __name__ == "__main__":
    unittest.main()
