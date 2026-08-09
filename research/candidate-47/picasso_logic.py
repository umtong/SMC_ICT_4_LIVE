"""Pure entry semantics from public Picasso RSI/BB/MACD strategy.

The source file has two distinct interpretations because Python evaluates ``&``
before ``|``.  ``source_exact`` reproduces the code as written: the first ADX
band can enter without trend or volume confirmation.  ``intended`` applies the
same thresholds but groups the two ADX bands before the shared directional and
volume confirmation.  No order, fill, account, PnL or optimization behavior
lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

ADX_LONG_MIN_1 = 5.7
ADX_LONG_MAX_1 = 6.5
ADX_LONG_MIN_2 = 20.9
ADX_LONG_MAX_2 = 50.7
ADX_SHORT_MIN_1 = 9.9
ADX_SHORT_MAX_1 = 21.4
ADX_SHORT_MIN_2 = 30.3
ADX_SHORT_MAX_2 = 50.8


@dataclass(frozen=True, slots=True)
class PicassoSnapshot:
    adx: float
    trend_l: int
    trend_s: int
    volume: float
    volume_mean_long: float
    volume_mean_short: float
    pnd_volume_warn: int = 0


@dataclass(frozen=True, slots=True)
class PicassoEntryDecision:
    source_exact_long: bool
    source_exact_short: bool
    intended_long: bool
    intended_short: bool
    long_first_band: bool
    long_second_band: bool
    short_first_band: bool
    short_second_band: bool
    pump_dump_suppressed: bool


def picasso_entry_decision(snapshot: PicassoSnapshot) -> PicassoEntryDecision:
    values = (
        float(snapshot.adx),
        float(snapshot.volume),
        float(snapshot.volume_mean_long),
        float(snapshot.volume_mean_short),
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Picasso snapshot contains non-finite input")
    if snapshot.volume < 0.0 or snapshot.volume_mean_long < 0.0 or snapshot.volume_mean_short < 0.0:
        raise ValueError("Picasso volume inputs cannot be negative")
    if snapshot.trend_l not in (-1, 0, 1) or snapshot.trend_s not in (-1, 0, 1):
        raise ValueError("Picasso trend states must be -1, 0 or 1")

    long_first = ADX_LONG_MIN_1 < snapshot.adx < ADX_LONG_MAX_1
    long_second = ADX_LONG_MIN_2 < snapshot.adx < ADX_LONG_MAX_2
    short_first = ADX_SHORT_MIN_1 < snapshot.adx < ADX_SHORT_MAX_1
    short_second = ADX_SHORT_MIN_2 < snapshot.adx < ADX_SHORT_MAX_2

    long_confirmation = (
        snapshot.trend_l == 1
        and snapshot.volume > snapshot.volume_mean_long
        and snapshot.volume > 0.0
    )
    short_confirmation = (
        snapshot.trend_s == -1
        and snapshot.volume > snapshot.volume_mean_short
    )

    # Exact Python semantics of the public source: ``&`` binds before ``|``.
    exact_long = long_first or (long_second and long_confirmation)
    exact_short = short_first or (short_second and short_confirmation)

    # The coherent interpretation of the same ingredients and thresholds.
    intended_long = (long_first or long_second) and long_confirmation
    intended_short = (short_first or short_second) and short_confirmation

    suppressed = int(snapshot.pnd_volume_warn) < 0
    if suppressed:
        exact_long = exact_short = intended_long = intended_short = False

    return PicassoEntryDecision(
        source_exact_long=exact_long,
        source_exact_short=exact_short,
        intended_long=intended_long,
        intended_short=intended_short,
        long_first_band=long_first,
        long_second_band=long_second,
        short_first_band=short_first,
        short_second_band=short_second,
        pump_dump_suppressed=suppressed,
    )


__all__ = [
    "PicassoEntryDecision",
    "PicassoSnapshot",
    "picasso_entry_decision",
]
