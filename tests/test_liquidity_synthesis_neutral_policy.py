from __future__ import annotations

from itertools import permutations

import pytest

from smc_ict_4.episode_policy_live.domain import Bar
from smc_ict_4.episode_policy_live.neutral_policy import (
    ExecutionFeedback,
    IntentValidity,
    MARKET_SYMBOLS,
    MarketFrame,
    OrderIntent,
    PolicyOutput,
    SynchronizedMarketFrameBuffer,
    TradingPolicy,
)


MINUTE = 60_000_000_000


def bars(minute: int = 1) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            symbol=symbol,
            interval_minutes=1,
            open_time_ns=(minute - 1) * MINUTE,
            close_time_ns=minute * MINUTE,
            open=100.0 + serial,
            high=101.0 + serial,
            low=99.0 + serial,
            close=100.5 + serial,
            volume=10.0,
            quote_volume=1_000.0,
            taker_buy_quote_volume=510.0,
            trade_count=20,
        )
        for serial, symbol in enumerate(MARKET_SYMBOLS)
    )


def test_market_frame_requires_exact_synchronized_universe() -> None:
    frame = MarketFrame(tuple(reversed(bars())))
    assert tuple(bar.symbol for bar in frame.bars) == tuple(sorted(MARKET_SYMBOLS))
    assert frame.close_time_ns == MINUTE
    assert frame.bar("SOLUSDT").symbol == "SOLUSDT"

    with pytest.raises(ValueError, match="exactly four"):
        MarketFrame(bars()[:-1])
    shifted = list(bars())
    shifted[-1] = bars(2)[-1]
    with pytest.raises(ValueError, match="completed interval"):
        MarketFrame(tuple(shifted))


def test_order_intent_has_no_legacy_family_or_evidence_contract() -> None:
    long = OrderIntent("long", "BTCUSDT", "LONG", 10, 100, 99, 101, 20)
    short = OrderIntent("short", "ETHUSDT", "SHORT", 10, 100, 101, 99, 20)
    assert long.gross_rr == 1.0
    assert short.gross_rr == 1.0
    assert not hasattr(long, "family")
    assert not hasattr(long, "evidence")

    structurally_valid = OrderIntent("structural", "SOLUSDT", "LONG", 10, 100, 99, 102)
    assert structurally_valid.valid_until_ns is None

    with pytest.raises(ValueError, match="at least 1.0"):
        OrderIntent("bad", "BTCUSDT", "LONG", 10, 100, 99, 100.5, 20)
    with pytest.raises(ValueError, match="stop < entry < target"):
        OrderIntent("bad", "BTCUSDT", "LONG", 10, 100, 101, 102, 20)


def test_feedback_validity_and_policy_protocol_are_execution_neutral() -> None:
    class ExamplePolicy:
        def on_market_frame(self, frame: MarketFrame) -> PolicyOutput:
            return PolicyOutput()

        def on_execution_feedback(self, feedback: ExecutionFeedback) -> None:
            self.feedback = feedback

    policy = ExamplePolicy()
    assert isinstance(policy, TradingPolicy)
    feedback = ExecutionFeedback("intent", MINUTE, "FILLED", 100.0, 2.0)
    policy.on_execution_feedback(feedback)
    assert policy.feedback == feedback
    assert IntentValidity("intent", True).valid
    with pytest.raises(ValueError, match="requires a reason"):
        IntentValidity("intent", False)


def test_buffer_is_invariant_to_symbol_arrival_permutation() -> None:
    expected = MarketFrame(bars())
    for order in permutations(bars()):
        buffer = SynchronizedMarketFrameBuffer()
        emitted = tuple(frame for bar in order for frame in buffer.push(bar))
        assert emitted == (expected,)


def test_buffer_waits_for_earliest_frame_and_drains_ready_frames_in_order() -> None:
    buffer = SynchronizedMarketFrameBuffer()
    first, second = bars(1), bars(2)
    for bar in first[:-1]:
        assert buffer.push(bar) == ()
    for bar in second:
        assert buffer.push(bar) == ()
    assert buffer.push(first[-1]) == (MarketFrame(first), MarketFrame(second))


def test_buffer_snapshot_is_canonical_and_restores_partial_frame() -> None:
    buffer = SynchronizedMarketFrameBuffer()
    first = bars()
    for bar in reversed(first[:3]):
        buffer.push(bar)
    snapshot = buffer.snapshot()
    restored = SynchronizedMarketFrameBuffer.from_snapshot(snapshot)
    assert restored.snapshot() == snapshot
    assert restored.push(first[-1]) == (MarketFrame(first),)
    assert restored.push(first[-1]) == ()


def test_buffer_rejects_conflicting_duplicates() -> None:
    buffer = SynchronizedMarketFrameBuffer()
    original = bars()[0]
    assert buffer.push(original) == ()
    conflict = Bar.from_dict({**original.to_dict(), "close": original.close + 0.1})
    with pytest.raises(ValueError, match="conflicting duplicate"):
        buffer.push(conflict)
