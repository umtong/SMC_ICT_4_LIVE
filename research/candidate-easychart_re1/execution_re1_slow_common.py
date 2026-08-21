"""Fifteen-minute common-flow auction state for EasyChart RE1.

The prior cross-asset router treated each one-minute common shock as a market
state.  That was useful for rejecting obvious counter-factor trades, but the
latest-six-event history often covered only a few minutes and therefore could
not represent the tens-of-minutes-to-hours inventory programmes targeted by the
system.

This module changes the observation clock rather than adding another entry
filter:

* exact completed Binance one-minute taker fields are accumulated into causal
  fifteen-minute symbol buckets;
* a symbol contributes a directional event only when cumulative signed taker
  quote and net price progress agree and absolute signed flow is at least the
  median of its previous sixteen completed fifteen-minute buckets;
* BTC and ETH plus at least three of BTC/ETH/SOL/XRP must agree before a common
  event exists;
* the latest six completed common fifteen-minute events classify persistent,
  transitional and turbulent control states through the existing router;
* an event remains active only while BTC, ETH and at least three symbols hold
  beyond the event-bucket midpoint at a later completed fifteen-minute close.

No wall-clock timeout, fitted magnitude, volatility score, partial exit or risk
change is introduced.  The slower state supplies context; the scenario engine
still owns location, entry, invalidation and objective.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Any

import contracts_v5 as _contracts
from domain import Side
from easychart_re1_flow import FlowCandle
from easychart_re1_turbulent_contraction import FullAuctionStateStrategy
from execution_re1_factor_persistence import (
    CommonAuctionEvent,
    CommonAuctionRegime,
    CommonAuctionSnapshot,
    EasyChartRE1PersistentFactorStrategy,
)
from execution_re1_market_factor import CommonFactorState


SLOW_COMMON_AUCTION_RULE = (
    "EXTERNAL_METHOD:COMPLETED_FIFTEEN_MINUTE_CUMULATIVE_TAKER_FLOW_AND_PRICE_PROGRESS_DEFINE_THE_COMMON_CRYPTO_AUCTION_CLOCK"
)
SLOW_COMMON_MATERIALITY_RULE = (
    "RESEARCH_HYPOTHESIS:A_SYMBOL_COMMON_EVENT_REQUIRES_ABSOLUTE_FIFTEEN_MINUTE_SIGNED_TAKER_FLOW_AT_LEAST_ITS_PRIOR_SIXTEEN_BUCKET_MEDIAN"
)
SLOW_COMMON_HOLD_RULE = (
    "RESEARCH_HYPOTHESIS:A_FIFTEEN_MINUTE_COMMON_EVENT_REMAINS_ACTIVE_ONLY_WHILE_BTC_ETH_AND_THREE_OF_FOUR_HOLD_ITS_EVENT_MIDPOINT_AT_A_LATER_FIFTEEN_MINUTE_CLOSE"
)
if SLOW_COMMON_AUCTION_RULE not in _contracts.EXTERNAL_RULES:
    _contracts.EXTERNAL_RULES += (SLOW_COMMON_AUCTION_RULE,)
for _rule in (SLOW_COMMON_MATERIALITY_RULE, SLOW_COMMON_HOLD_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class SlowSymbolAuction:
    symbol: str
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    signed_taker_quote: float
    trade_count: int
    prior_median_abs_delta: float
    prior_median_quote_volume: float
    side: Side | None

    @property
    def midpoint(self) -> float:
        return (self.open + self.close) / 2.0

    @property
    def net_progress(self) -> float:
        return self.close - self.open

    @property
    def delta_share(self) -> float:
        return self.signed_taker_quote / max(self.quote_volume, 1e-12)

    @property
    def delta_ratio(self) -> float:
        return abs(self.signed_taker_quote) / max(self.prior_median_abs_delta, 1e-12)

    @property
    def activity_ratio(self) -> float:
        return self.quote_volume / max(self.prior_median_quote_volume, 1e-12)


class SlowCommonAuctionMixin:
    """Replace one-minute common shocks with completed fifteen-minute auctions."""

    SLOW_MINUTES = 15
    NS_PER_MINUTE = 60_000_000_000
    SLOW_BASELINE_BUCKETS = 16
    SLOW_RAW_BARS = 240
    SLOW_BUCKET_HISTORY = 192

    def on_start(self) -> None:
        super().on_start()
        self.slow_common_minute_bars: dict[str, deque[FlowCandle]] = {
            symbol: deque(maxlen=self.SLOW_RAW_BARS)
            for symbol in self.factor_symbols.values()
        }
        self.slow_symbol_history: dict[str, deque[SlowSymbolAuction]] = {
            symbol: deque(maxlen=self.SLOW_BUCKET_HISTORY)
            for symbol in self.factor_symbols.values()
        }
        self.slow_common_counts: dict[str, int] = {}

    def _slow_inc(self, key: str) -> None:
        self.slow_common_counts[key] = self.slow_common_counts.get(key, 0) + 1

    @classmethod
    def _is_slow_close(cls, ts_ns: int) -> bool:
        return (ts_ns // cls.NS_PER_MINUTE) % cls.SLOW_MINUTES == 0

    @classmethod
    def _consecutive_bucket(cls, bars: list[FlowCandle], ts_ns: int) -> bool:
        if len(bars) != cls.SLOW_MINUTES or bars[-1].ts_close_ns != ts_ns:
            return False
        return all(
            bars[index].ts_close_ns - bars[index - 1].ts_close_ns == cls.NS_PER_MINUTE
            for index in range(1, len(bars))
        )

    def _aggregate_symbol(
        self,
        symbol: str,
        ts_ns: int,
    ) -> SlowSymbolAuction | None:
        bars = list(self.slow_common_minute_bars[symbol])[-self.SLOW_MINUTES :]
        if not self._consecutive_bucket(bars, ts_ns):
            self._slow_inc("slow_incomplete_symbol_bucket")
            return None
        history = self.slow_symbol_history[symbol]
        if len(history) < self.SLOW_BASELINE_BUCKETS:
            self._slow_inc("slow_bucket_baseline_warmup")
            prior_abs_delta = 0.0
            prior_quote = 0.0
            side = None
        else:
            prior = list(history)[-self.SLOW_BASELINE_BUCKETS :]
            prior_abs_delta = median(abs(item.signed_taker_quote) for item in prior)
            prior_quote = median(item.quote_volume for item in prior)
            cumulative_delta = sum(
                2.0 * item.taker_buy_quote_volume - item.quote_volume
                for item in bars
            )
            progress = bars[-1].close - bars[0].open
            material = abs(cumulative_delta) >= max(prior_abs_delta, 1e-12)
            if material and cumulative_delta > 0.0 and progress > 0.0:
                side = Side.LONG
            elif material and cumulative_delta < 0.0 and progress < 0.0:
                side = Side.SHORT
            else:
                side = None
        cumulative_delta = sum(
            2.0 * item.taker_buy_quote_volume - item.quote_volume
            for item in bars
        )
        auction = SlowSymbolAuction(
            symbol=symbol,
            ts_close_ns=ts_ns,
            open=bars[0].open,
            high=max(item.high for item in bars),
            low=min(item.low for item in bars),
            close=bars[-1].close,
            quote_volume=sum(item.quote_volume for item in bars),
            signed_taker_quote=cumulative_delta,
            trade_count=sum(item.trade_count for item in bars),
            prior_median_abs_delta=prior_abs_delta,
            prior_median_quote_volume=prior_quote,
            side=side,
        )
        history.append(auction)
        self._slow_inc("slow_symbol_bucket_completed")
        if side is not None:
            self._slow_inc(f"slow_symbol_{side.name.lower()}_event")
        return auction

    @staticmethod
    def _held_beyond(side: Side, auction: SlowSymbolAuction, midpoint: float) -> bool:
        return auction.close > midpoint if side is Side.LONG else auction.close < midpoint

    def _record_history_event(self) -> None:
        state = self.factor_state
        if state is None or state.event_time_ns == self._last_recorded_factor_time_ns:
            return
        self._last_recorded_factor_time_ns = state.event_time_ns
        self.common_event_history.append(
            CommonAuctionEvent(
                side=state.side,
                event_time_ns=state.event_time_ns,
                agreeing_symbols=tuple(state.agreeing_symbols),
                sequence=state.sequence,
            )
        )
        self._pinc("slow_common_event_recorded")

    def _auction_snapshot(self) -> CommonAuctionSnapshot:
        snapshot = super()._auction_snapshot()
        # A stale historical flip count must not keep the account in TURBULENT
        # abstention after the last common auction has relinquished its midpoint.
        if self.factor_state is not None:
            return snapshot
        return CommonAuctionSnapshot(
            regime=CommonAuctionRegime.UNKNOWN,
            side=None,
            flips=snapshot.flips,
            events=snapshot.events,
            latest_event_time_ns=snapshot.latest_event_time_ns,
            latest_agreeing_symbols=snapshot.latest_agreeing_symbols,
            active_side=None,
            active_event_time_ns=None,
        )

    def _observe_common_factor(self) -> None:
        one_minute = {
            instrument_id: bar
            for instrument_id, timeframe, bar in self.bar_bucket
            if timeframe == self.EXECUTION_MINUTES
        }
        if len(one_minute) != len(self.config.instrument_ids):
            self._slow_inc("slow_incomplete_one_minute_bucket")
            self._publish_snapshot(self._auction_snapshot())
            return

        candles: dict[str, FlowCandle] = {}
        for instrument_id, bar in sorted(one_minute.items(), key=lambda item: str(item[0])):
            symbol = self.factor_symbols[instrument_id]
            candle = self._candle(bar)
            candles[symbol] = candle
            self.factor_analyzers[instrument_id].observe(candle)
            self.slow_common_minute_bars[symbol].append(candle)

        ts_ns = int(self.bar_bucket_ts or 0)
        if not self._is_slow_close(ts_ns):
            self._publish_snapshot(self._auction_snapshot())
            return

        auctions: dict[str, SlowSymbolAuction] = {}
        for symbol in sorted(candles):
            item = self._aggregate_symbol(symbol, ts_ns)
            if item is not None:
                auctions[symbol] = item
        if len(auctions) != len(self.config.instrument_ids):
            self._slow_inc("slow_incomplete_cross_asset_bucket")
            self._publish_snapshot(self._auction_snapshot())
            return

        common_side: Side | None = None
        agreeing: tuple[str, ...] = ()
        for side in (Side.LONG, Side.SHORT):
            members = tuple(sorted(symbol for symbol, item in auctions.items() if item.side is side))
            if (
                auctions["BTCUSDT"].side is side
                and auctions["ETHUSDT"].side is side
                and len(members) >= 3
            ):
                common_side = side
                agreeing = members
                break

        if common_side is not None:
            previous = self.factor_state
            sequence = (
                previous.sequence + 1
                if previous is not None and previous.side is common_side
                else 1
            )
            self.factor_state = CommonFactorState(
                side=common_side,
                event_time_ns=ts_ns,
                event_midpoints={symbol: item.midpoint for symbol, item in auctions.items()},
                agreeing_symbols=agreeing,
                sequence=sequence,
            )
            self._slow_inc(
                "slow_common_same_side_refreshed"
                if sequence > 1
                else "slow_common_started"
            )
            self._record(
                "slow_market_factor_common_auction",
                event_time_ns=ts_ns,
                side=common_side.name,
                agreeing_symbols=list(agreeing),
                sequence=sequence,
                auctions={
                    symbol: {
                        "side": None if item.side is None else item.side.name,
                        "quote_volume": item.quote_volume,
                        "signed_taker_quote": item.signed_taker_quote,
                        "delta_share": item.delta_share,
                        "delta_ratio": item.delta_ratio,
                        "activity_ratio": item.activity_ratio,
                        "net_progress": item.net_progress,
                        "midpoint": item.midpoint,
                    }
                    for symbol, item in sorted(auctions.items())
                },
                rule_provenance=(
                    SLOW_COMMON_AUCTION_RULE,
                    SLOW_COMMON_MATERIALITY_RULE,
                ),
            )
            self._record_history_event()
            self._publish_snapshot(self._auction_snapshot())
            return

        state = self.factor_state
        if state is not None:
            held = tuple(
                sorted(
                    symbol
                    for symbol, item in auctions.items()
                    if self._held_beyond(state.side, item, state.event_midpoints[symbol])
                )
            )
            if "BTCUSDT" in held and "ETHUSDT" in held and len(held) >= 3:
                self._slow_inc("slow_common_midpoint_hold")
            else:
                self._record(
                    "slow_market_factor_state_ended",
                    event_time_ns=ts_ns,
                    side=state.side.name,
                    origin_time_ns=state.event_time_ns,
                    held_symbols=list(held),
                    sequence=state.sequence,
                    reason="BTC_ETH_OR_THREE_OF_FOUR_LOST_FIFTEEN_MINUTE_EVENT_MIDPOINT",
                    rule_provenance=SLOW_COMMON_HOLD_RULE,
                )
                self._slow_inc("slow_common_midpoint_lost")
                self.factor_state = None

        self._publish_snapshot(self._auction_snapshot())

    @property
    def slow_common_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self.slow_common_counts.items())),
            "bucket_minutes": self.SLOW_MINUTES,
            "baseline_buckets": self.SLOW_BASELINE_BUCKETS,
            "rules": (
                SLOW_COMMON_AUCTION_RULE,
                SLOW_COMMON_MATERIALITY_RULE,
                SLOW_COMMON_HOLD_RULE,
            ),
        }


class SlowPersistentFactorStrategy(
    SlowCommonAuctionMixin,
    EasyChartRE1PersistentFactorStrategy,
):
    """Quality/direct families routed by the slow common-auction clock."""


class SlowFullAuctionStateStrategy(
    SlowCommonAuctionMixin,
    FullAuctionStateStrategy,
):
    """Persistent continuation and turbulent contraction under the slow clock."""
