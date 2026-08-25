"""Causal price-and-aggressor-flow ownership for one synchronized interval.

The observer separates a symbol's delivery from the median delivery of its
three peers.  It is deliberately categorical: classification uses only zero
boundaries and ordinal local-versus-common relationships, never calibrated
magnitude thresholds.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math
from statistics import fmean, median
from typing import Mapping, Sequence

from .domain import Bar, SYMBOLS


PRE_INTERVAL_ATR_BARS = 14


class FlowPriceDeliveryRole(StrEnum):
    """Who, if anyone, owns delivery in the proposed direction."""

    LOCAL_PRICE_DISCOVERY = "LOCAL_PRICE_DISCOVERY"
    COMMON_REPRICING = "COMMON_REPRICING"
    AGGRESSION_WITHOUT_PROGRESS = "AGGRESSION_WITHOUT_PROGRESS"
    OPPOSED = "OPPOSED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class MarketFlowPriceFact:
    """One market's side-adjusted, causally normalized interval facts."""

    symbol: str
    atr: float | None
    raw_price_change: float | None
    price_units: float | None
    signed_taker_quote: float | None
    total_quote: float | None
    flow_ratio: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FlowPriceDeliveryObservation:
    """Local, peer-common and residual delivery at a completed decision time."""

    symbol: str
    side: str
    interval_start_ns: int
    interval_end_ns: int
    observed_time_ns: int
    role: FlowPriceDeliveryRole
    local_price_units: float | None
    common_price_units: float | None
    residual_price_units: float | None
    local_flow_ratio: float | None
    common_flow_ratio: float | None
    residual_flow_ratio: float | None
    market_facts: tuple[MarketFlowPriceFact, ...]
    missing_evidence: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.role is not FlowPriceDeliveryRole.UNKNOWN

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "interval_start_ns": self.interval_start_ns,
            "interval_end_ns": self.interval_end_ns,
            "observed_time_ns": self.observed_time_ns,
            "role": self.role.value,
            "known": self.known,
            "local_price_units": self.local_price_units,
            "common_price_units": self.common_price_units,
            "residual_price_units": self.residual_price_units,
            "local_flow_ratio": self.local_flow_ratio,
            "common_flow_ratio": self.common_flow_ratio,
            "residual_flow_ratio": self.residual_flow_ratio,
            "market_facts": [item.to_dict() for item in self.market_facts],
            "missing_evidence": self.missing_evidence,
        }


def _side_contract(side: str) -> tuple[str, float]:
    normalized = side.upper()
    if normalized in {"LONG", "BUY"}:
        return "LONG", 1.0
    if normalized in {"SHORT", "SELL"}:
        return "SHORT", -1.0
    raise ValueError("side must be LONG/BUY or SHORT/SELL")


def _ordered_history(symbol: str, bars: Sequence[Bar]) -> tuple[Bar, ...]:
    ordered = tuple(sorted(bars, key=lambda item: item.open_time_ns))
    previous_open = -1
    previous_close = -1
    for bar in ordered:
        if bar.symbol != symbol:
            raise ValueError(f"history key {symbol} contains {bar.symbol}")
        if bar.interval_minutes != 1:
            raise ValueError("flow-price delivery requires completed 1m bars")
        if bar.open_time_ns <= previous_open or bar.close_time_ns <= previous_close:
            raise ValueError("bar histories must have unique increasing timestamps")
        previous_open = bar.open_time_ns
        previous_close = bar.close_time_ns
    return ordered


def _pre_interval_atr(
    bars: Sequence[Bar],
    *,
    interval_start_ns: int,
) -> float | None:
    completed_indices = [
        index for index, bar in enumerate(bars) if bar.close_time_ns <= interval_start_ns
    ]
    if not completed_indices:
        return None
    selected = completed_indices[-PRE_INTERVAL_ATR_BARS:]
    true_ranges: list[float] = []
    for index in selected:
        bar = bars[index]
        if index > 0:
            previous_close = bars[index - 1].close
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        else:
            true_range = bar.high - bar.low
        if math.isfinite(true_range) and true_range >= 0.0:
            true_ranges.append(float(true_range))
    if not true_ranges:
        return None
    value = float(fmean(true_ranges))
    return value if math.isfinite(value) and value > 0.0 else None


def _market_fact(
    symbol: str,
    bars: Sequence[Bar],
    *,
    interval_start_ns: int,
    interval_end_ns: int,
    sign: float,
) -> tuple[MarketFlowPriceFact, tuple[tuple[int, int], ...]]:
    atr = _pre_interval_atr(bars, interval_start_ns=interval_start_ns)
    event = tuple(
        bar
        for bar in bars
        if bar.open_time_ns >= interval_start_ns
        and bar.close_time_ns <= interval_end_ns
    )
    timestamps = tuple((bar.open_time_ns, bar.close_time_ns) for bar in event)
    if not event or atr is None:
        return (
            MarketFlowPriceFact(symbol, atr, None, None, None, None, None),
            timestamps,
        )

    raw_price_change = float(event[-1].close - event[0].open)
    price_units = sign * raw_price_change / atr
    signed_taker_quote = float(sum(bar.signed_quote_flow for bar in event))
    total_quote = float(sum(bar.quote_volume for bar in event))
    flow_ratio = (
        sign * signed_taker_quote / total_quote
        if math.isfinite(total_quote) and total_quote > 0.0
        else None
    )
    if not all(
        math.isfinite(value)
        for value in (raw_price_change, price_units, signed_taker_quote)
    ):
        return (
            MarketFlowPriceFact(symbol, atr, None, None, None, total_quote, None),
            timestamps,
        )
    return (
        MarketFlowPriceFact(
            symbol=symbol,
            atr=atr,
            raw_price_change=raw_price_change,
            price_units=price_units,
            signed_taker_quote=signed_taker_quote,
            total_quote=total_quote,
            flow_ratio=flow_ratio,
        ),
        timestamps,
    )


def _classify(
    *,
    local_price: float,
    common_price: float,
    residual_price: float,
    local_flow: float,
    common_flow: float,
    residual_flow: float,
) -> FlowPriceDeliveryRole:
    # Positive aggressor residual without positive price residual is failed
    # delivery, even when the outright price move is opposed.
    if local_flow > 0.0 and residual_flow > 0.0 and residual_price <= 0.0:
        return FlowPriceDeliveryRole.AGGRESSION_WITHOUT_PROGRESS
    if local_price < 0.0 or local_flow < 0.0:
        return FlowPriceDeliveryRole.OPPOSED
    if residual_price > 0.0 and residual_flow > 0.0:
        return FlowPriceDeliveryRole.LOCAL_PRICE_DISCOVERY
    if (
        local_price > 0.0
        and local_flow > 0.0
        and common_price > 0.0
        and common_flow > 0.0
    ):
        return FlowPriceDeliveryRole.COMMON_REPRICING
    return FlowPriceDeliveryRole.UNKNOWN


def observe_flow_price_delivery(
    *,
    symbol: str,
    side: str,
    interval_start_ns: int,
    interval_end_ns: int,
    histories: Mapping[str, Sequence[Bar]],
) -> FlowPriceDeliveryObservation:
    """Observe causal local-minus-common delivery over synchronized 1m bars.

    Price change is divided by each market's 14-bar arithmetic ATR, calculated
    exclusively from bars completed before ``interval_start_ns``.  Aggressor
    flow is signed taker quote divided by total quote.  Both are side-adjusted
    before peer medians and local residuals are calculated.
    """

    if symbol not in SYMBOLS:
        raise ValueError(f"unsupported symbol: {symbol}")
    normalized_side, sign = _side_contract(side)
    if interval_start_ns < 0 or interval_end_ns <= interval_start_ns:
        raise ValueError("interval_end_ns must be after interval_start_ns")

    missing: list[str] = []
    ordered: dict[str, tuple[Bar, ...]] = {}
    for market in SYMBOLS:
        if market not in histories:
            missing.append(f"MISSING_HISTORY:{market}")
            ordered[market] = ()
            continue
        ordered[market] = _ordered_history(market, histories[market])

    facts: list[MarketFlowPriceFact] = []
    timestamps_by_market: dict[str, tuple[tuple[int, int], ...]] = {}
    for market in SYMBOLS:
        fact, timestamps = _market_fact(
            market,
            ordered[market],
            interval_start_ns=interval_start_ns,
            interval_end_ns=interval_end_ns,
            sign=sign,
        )
        facts.append(fact)
        timestamps_by_market[market] = timestamps
        if not timestamps:
            missing.append(f"NO_INTERVAL_BARS:{market}")
        if fact.atr is None:
            missing.append(f"NO_PRE_INTERVAL_ATR:{market}")
        if fact.flow_ratio is None:
            missing.append(f"NO_INTERVAL_QUOTE_FLOW:{market}")

    timestamp_paths = tuple(timestamps_by_market[market] for market in SYMBOLS)
    if timestamp_paths and any(path != timestamp_paths[0] for path in timestamp_paths[1:]):
        missing.append("UNSYNCHRONIZED_INTERVAL_BARS")
    if timestamp_paths and timestamp_paths[0]:
        if timestamp_paths[0][0][0] != interval_start_ns:
            missing.append("INTERVAL_START_NOT_COVERED")
        if timestamp_paths[0][-1][1] != interval_end_ns:
            missing.append("INTERVAL_END_NOT_COVERED")

    fact_by_symbol = {fact.symbol: fact for fact in facts}
    local = fact_by_symbol[symbol]
    peers = [fact_by_symbol[market] for market in SYMBOLS if market != symbol]
    complete = (
        not missing
        and local.price_units is not None
        and local.flow_ratio is not None
        and all(item.price_units is not None for item in peers)
        and all(item.flow_ratio is not None for item in peers)
    )
    if not complete:
        return FlowPriceDeliveryObservation(
            symbol=symbol,
            side=normalized_side,
            interval_start_ns=interval_start_ns,
            interval_end_ns=interval_end_ns,
            observed_time_ns=interval_end_ns,
            role=FlowPriceDeliveryRole.UNKNOWN,
            local_price_units=local.price_units,
            common_price_units=None,
            residual_price_units=None,
            local_flow_ratio=local.flow_ratio,
            common_flow_ratio=None,
            residual_flow_ratio=None,
            market_facts=tuple(facts),
            missing_evidence=tuple(dict.fromkeys(missing)),
        )

    local_price = float(local.price_units)
    local_flow = float(local.flow_ratio)
    common_price = float(median(float(item.price_units) for item in peers))
    common_flow = float(median(float(item.flow_ratio) for item in peers))
    residual_price = local_price - common_price
    residual_flow = local_flow - common_flow
    role = _classify(
        local_price=local_price,
        common_price=common_price,
        residual_price=residual_price,
        local_flow=local_flow,
        common_flow=common_flow,
        residual_flow=residual_flow,
    )
    missing_evidence = (
        ("ZERO_OR_MIXED_ORDINAL_RELATION",)
        if role is FlowPriceDeliveryRole.UNKNOWN
        else ()
    )
    return FlowPriceDeliveryObservation(
        symbol=symbol,
        side=normalized_side,
        interval_start_ns=interval_start_ns,
        interval_end_ns=interval_end_ns,
        observed_time_ns=interval_end_ns,
        role=role,
        local_price_units=local_price,
        common_price_units=common_price,
        residual_price_units=residual_price,
        local_flow_ratio=local_flow,
        common_flow_ratio=common_flow,
        residual_flow_ratio=residual_flow,
        market_facts=tuple(facts),
        missing_evidence=missing_evidence,
    )
