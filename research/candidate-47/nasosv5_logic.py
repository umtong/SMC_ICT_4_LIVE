"""Pure exact-default entry logic from public NASOSv5.

No order, fill, position, account or PnL behavior lives here.  The module exists
only to pin the public source constants and prove that each source branch and
the 15-minute objective-space suppression are independently reachable.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

BASE_NB_CANDLES_BUY = 8
BASE_NB_CANDLES_SELL = 16
LOW_OFFSET = 0.981
LOW_OFFSET_2 = 0.942
HIGH_OFFSET = 1.097
EWO_HIGH = 3.553
EWO_HIGH_2 = -5.585
EWO_LOW = -14.378
RSI_BUY = 78.0
RSI_FAST_BUY = 37.0
LOOKBACK_15M = 32
PROFIT_THRESHOLD = 1.037


@dataclass(frozen=True, slots=True)
class NasosSnapshot:
    close: float
    low: float
    volume: float
    ema_buy: float
    ema_sell: float
    ema_fast_ewo: float
    ema_slow_ewo: float
    rsi_fast: float
    rsi: float
    objective_15m_high: float


@dataclass(frozen=True, slots=True)
class NasosDecision:
    ewo1: bool
    ewo2: bool
    ewolow: bool
    raw_entry: bool
    suppressed_no_profit_space: bool
    actionable: bool
    ewo: float
    profit_space: float
    tag: str


def exact_nasosv5_decision(snapshot: NasosSnapshot) -> NasosDecision:
    values = tuple(float(getattr(snapshot, field)) for field in snapshot.__dataclass_fields__)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("NASOSv5 snapshot contains non-finite input")
    if (
        snapshot.close <= 0.0
        or snapshot.low <= 0.0
        or snapshot.volume < 0.0
        or snapshot.ema_buy <= 0.0
        or snapshot.ema_sell <= 0.0
        or snapshot.objective_15m_high <= 0.0
    ):
        raise ValueError("NASOSv5 snapshot contains non-positive price or negative volume")

    ewo = (snapshot.ema_fast_ewo - snapshot.ema_slow_ewo) / snapshot.low * 100.0
    common = (
        snapshot.rsi_fast < RSI_FAST_BUY
        and snapshot.volume > 0.0
        and snapshot.close < snapshot.ema_sell * HIGH_OFFSET
    )
    ewo1 = (
        common
        and snapshot.close < snapshot.ema_buy * LOW_OFFSET
        and ewo > EWO_HIGH
        and snapshot.rsi < RSI_BUY
    )
    ewo2 = (
        common
        and snapshot.close < snapshot.ema_buy * LOW_OFFSET_2
        and ewo > EWO_HIGH_2
        and snapshot.rsi < RSI_BUY
        and snapshot.rsi < 25.0
    )
    ewolow = (
        common
        and snapshot.close < snapshot.ema_buy * LOW_OFFSET
        and ewo < EWO_LOW
    )
    raw = ewo1 or ewo2 or ewolow
    suppressed = snapshot.objective_15m_high < snapshot.close * PROFIT_THRESHOLD
    actionable = raw and not suppressed
    tag = " ".join(
        name
        for name, enabled in (
            ("ewo1", ewo1),
            ("ewo2", ewo2),
            ("ewolow", ewolow),
        )
        if enabled
    ) or "none"
    return NasosDecision(
        ewo1=ewo1,
        ewo2=ewo2,
        ewolow=ewolow,
        raw_entry=raw,
        suppressed_no_profit_space=suppressed,
        actionable=actionable,
        ewo=ewo,
        profit_space=snapshot.objective_15m_high / snapshot.close - 1.0,
        tag=tag,
    )


__all__ = [
    "NasosDecision",
    "NasosSnapshot",
    "exact_nasosv5_decision",
]
