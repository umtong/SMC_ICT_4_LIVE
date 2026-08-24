"""Causal ownership of a synchronized directional market episode.

Ownership is a routing primitive, not a trade filter.  For each supported
market the completed interval return is signed into the proposed direction and
normalised by volatility known *before* the interval began.  The median of the
other three markets is the counterfactual common-market delivery; the
difference between local and common delivery is the local residual.

The implementation deliberately has no magnitude cut-off, fitted score, or
special leader market.  Zero is the only classification boundary.  A common
move is routed to a common-market episode instead of being silently rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log
from statistics import median
from typing import Mapping, Sequence

from .cross_market_roles import SourceOwnershipRole
from .domain import Bar, PolicyError, SYMBOLS, stable_id


ATR_LENGTH = 20
_SIDES = {"LONG": 1.0, "SHORT": -1.0}


@dataclass(frozen=True, slots=True)
class CausalAtrSnapshot:
    """Volatility estimate whose entire sample predates the owned interval."""

    symbol: str
    sample_open_time_ns: int | None
    sample_close_time_ns: int | None
    observed_time_ns: int
    sample_count: int
    price_at_sample_end: float | None
    atr_price: float | None
    atr_fraction: float | None

    @property
    def available(self) -> bool:
        return self.atr_fraction is not None and self.atr_fraction > 0.0


@dataclass(frozen=True, slots=True)
class SymbolIntervalOwnership:
    """Immutable ownership result for one sibling market."""

    symbol: str
    side: str
    interval_open_time_ns: int
    interval_close_time_ns: int
    interval_open: float
    interval_close: float
    raw_log_return: float
    signed_log_return: float
    atr: CausalAtrSnapshot
    local_delivery_units: float | None
    peer_common_units: float | None
    residual_local_units: float | None
    role: SourceOwnershipRole


@dataclass(frozen=True, slots=True)
class IntervalOwnershipSnapshot:
    """One synchronized physical interval and all four ownership decisions.

    ``campaign_root_id`` is independent of side and the selected sibling
    symbol.  When supplied from a structural source it also survives later
    evaluation intervals.  ``ownership_snapshot_id`` identifies this exact
    side and interval evaluation, while ``common_cascade_id`` remains shared
    by all sibling proposals rooted in the campaign.
    """

    campaign_root_id: str
    ownership_snapshot_id: str
    common_cascade_id: str
    side: str
    interval_open_time_ns: int
    interval_close_time_ns: int
    observed_time_ns: int
    ownership: tuple[SymbolIntervalOwnership, ...]

    def for_symbol(self, symbol: str) -> SymbolIntervalOwnership:
        if symbol not in SYMBOLS:
            raise PolicyError(f"unsupported symbol: {symbol}")
        for item in self.ownership:
            if item.symbol == symbol:
                return item
        raise PolicyError(f"ownership snapshot is missing {symbol}")

    @property
    def common_owned_symbols(self) -> tuple[str, ...]:
        return tuple(
            item.symbol
            for item in self.ownership
            if item.role is SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY
        )

    @property
    def local_owned_symbols(self) -> tuple[str, ...]:
        return tuple(
            item.symbol
            for item in self.ownership
            if item.role is SourceOwnershipRole.LOCAL_SOURCE_OWNER
        )


def _true_range_median(bars: Sequence[Bar]) -> float:
    ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        value = bar.range
        if previous_close is not None:
            value = max(value, abs(bar.high - previous_close), abs(bar.low - previous_close))
        ranges.append(value)
        previous_close = bar.close
    return float(median(ranges))


def _validate_history(
    *,
    symbol: str,
    bars: Sequence[Bar],
    observed_time_ns: int,
) -> tuple[Bar, ...]:
    ordered = tuple(bars)
    previous: Bar | None = None
    for bar in ordered:
        if bar.symbol != symbol:
            raise PolicyError(f"{symbol} history contains a {bar.symbol} bar")
        if bar.close_time_ns > observed_time_ns:
            raise PolicyError(f"{symbol} history contains a bar not yet observable")
        if previous is not None:
            if bar.open_time_ns <= previous.open_time_ns:
                raise PolicyError(f"{symbol} bars must be strictly ordered")
            if bar.open_time_ns < previous.close_time_ns:
                raise PolicyError(f"{symbol} bars overlap")
            if bar.interval_minutes != previous.interval_minutes:
                raise PolicyError(f"{symbol} history mixes bar intervals")
        previous = bar
    return ordered


def _causal_atr(
    *,
    symbol: str,
    prior_bars: Sequence[Bar],
    interval_open_time_ns: int,
    atr_length: int,
) -> CausalAtrSnapshot:
    if atr_length <= 0:
        raise PolicyError("atr_length must be positive")
    if any(bar.close_time_ns > interval_open_time_ns for bar in prior_bars):
        raise PolicyError("ATR sample must be fully observable before the interval")

    sample = tuple(prior_bars[-atr_length:])
    if len(sample) < atr_length:
        return CausalAtrSnapshot(
            symbol=symbol,
            sample_open_time_ns=(sample[0].open_time_ns if sample else None),
            sample_close_time_ns=(sample[-1].close_time_ns if sample else None),
            observed_time_ns=interval_open_time_ns,
            sample_count=len(sample),
            price_at_sample_end=(sample[-1].close if sample else None),
            atr_price=None,
            atr_fraction=None,
        )

    price = float(sample[-1].close)
    atr_price = _true_range_median(sample)
    atr_fraction = atr_price / price if price > 0.0 and atr_price > 0.0 else None
    return CausalAtrSnapshot(
        symbol=symbol,
        sample_open_time_ns=sample[0].open_time_ns,
        sample_close_time_ns=sample[-1].close_time_ns,
        observed_time_ns=interval_open_time_ns,
        sample_count=len(sample),
        price_at_sample_end=price,
        atr_price=(atr_price if atr_fraction is not None else None),
        atr_fraction=atr_fraction,
    )


def _classify(
    *,
    local_units: float | None,
    peer_units: Sequence[float | None],
) -> tuple[SourceOwnershipRole, float | None, float | None]:
    if local_units is None or len(peer_units) != len(SYMBOLS) - 1:
        return SourceOwnershipRole.UNKNOWN, None, None
    if any(value is None for value in peer_units):
        return SourceOwnershipRole.UNKNOWN, None, None

    local = float(local_units)
    peers = tuple(float(value) for value in peer_units if value is not None)
    if not isfinite(local) or any(not isfinite(value) for value in peers):
        raise PolicyError("ownership delivery units must be finite")
    common = float(median(peers))
    residual = local - common
    if local <= 0.0:
        role = SourceOwnershipRole.NO_DIRECTIONAL_DELIVERY
    elif sum(value > 0.0 for value in peers) >= 2:
        # Three or four aligned high-liquidity markets are a common control
        # leg even when one instrument marginally outperforms the peer median.
        # Relative leadership may choose the executable expression later; it
        # cannot relabel a broad physical move as locally originated.
        role = SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY
    elif residual > 0.0 or common <= 0.0:
        role = SourceOwnershipRole.LOCAL_SOURCE_OWNER
    else:
        role = SourceOwnershipRole.COMMON_MARKET_OWNER_ONLY
    return role, common, residual


def _event_fingerprint(
    *,
    event_bars: Mapping[str, Sequence[Bar]],
) -> tuple[tuple[str, tuple[tuple[int, int, str, str, str, str], ...]], ...]:
    # Hex floats make the physical identity stable across locale and repr.
    return tuple(
        (
            symbol,
            tuple(
                (
                    bar.open_time_ns,
                    bar.close_time_ns,
                    float(bar.open).hex(),
                    float(bar.high).hex(),
                    float(bar.low).hex(),
                    float(bar.close).hex(),
                )
                for bar in event_bars[symbol]
            ),
        )
        for symbol in SYMBOLS
    )


def _physical_interval_root_id(
    *,
    interval_open_time_ns: int,
    interval_close_time_ns: int,
    event_bars: Mapping[str, Sequence[Bar]],
) -> str:
    """Fallback root for callers which do not yet own a source campaign.

    Side is intentionally absent.  A caller which already has an immutable
    structural source should pass :func:`source_campaign_root_id` instead so a
    later evaluation interval also remains under the same source campaign.
    """

    return stable_id(
        "PHYSICAL_INTERVAL_CAMPAIGN_V2",
        interval_open_time_ns,
        interval_close_time_ns,
        _event_fingerprint(event_bars=event_bars),
        prefix="CAMPAIGN:",
    )


def source_campaign_root_id(
    *,
    source_identity: str,
    source_generation: int,
    interaction_time_ns: int,
) -> str:
    """Create one parent root for every hypothesis of a structural source.

    The source identity and generation must themselves be frozen when the
    physical source becomes observable.  Direction and subsequent evaluation
    times are deliberately excluded, allowing accepted continuation to become
    an opposite-side trap without creating a second causal campaign.
    """

    if not source_identity or not source_identity.strip():
        raise PolicyError("source_identity must be non-empty")
    if source_generation < 0:
        raise PolicyError("source_generation must be non-negative")
    if interaction_time_ns < 0:
        raise PolicyError("interaction_time_ns must be non-negative")
    return stable_id(
        "STRUCTURAL_SOURCE_CAMPAIGN_V1",
        source_identity,
        source_generation,
        interaction_time_ns,
        prefix="CAMPAIGN:",
    )


def _ownership_snapshot_id(
    *,
    campaign_root_id: str,
    side: str,
    interval_open_time_ns: int,
    interval_close_time_ns: int,
    event_bars: Mapping[str, Sequence[Bar]],
) -> str:
    return stable_id(
        "INTERVAL_OWNERSHIP_V1",
        campaign_root_id,
        side,
        interval_open_time_ns,
        interval_close_time_ns,
        _event_fingerprint(event_bars=event_bars),
        prefix="OWNERSHIP:",
    )


def _validate_campaign_root_id(value: str) -> str:
    prefix = "CAMPAIGN:"
    if not isinstance(value, str):
        raise PolicyError("campaign_root_id must be a string")
    digest = value[len(prefix):] if value.startswith(prefix) else ""
    if len(digest) != 20 or any(character not in "0123456789abcdef" for character in digest):
        raise PolicyError("campaign_root_id is not a causal campaign root")
    return value


def sibling_episode_id(
    *,
    campaign_root_id: str,
    symbol: str,
    family: str,
) -> str:
    """Build a symbol hypothesis ID without losing the shared causal root."""

    _validate_campaign_root_id(campaign_root_id)
    if symbol not in SYMBOLS:
        raise PolicyError(f"unsupported symbol: {symbol}")
    if not family:
        raise PolicyError("family must be non-empty")
    return stable_id(campaign_root_id, symbol, family, prefix="EPISODE:")


def common_cascade_id(*, campaign_root_id: str) -> str:
    """Return the shared common-market hypothesis ID for a campaign root."""

    _validate_campaign_root_id(campaign_root_id)
    return stable_id(campaign_root_id, "COMMON_CASCADE", prefix="CASCADE:")


def observe_interval_ownership(
    *,
    observed_bars_by_symbol: Mapping[str, Sequence[Bar]],
    side: str,
    interval_open_time_ns: int,
    interval_close_time_ns: int,
    observed_time_ns: int,
    atr_length: int = ATR_LENGTH,
    campaign_root_id: str | None = None,
) -> IntervalOwnershipSnapshot:
    """Observe symmetric ownership of one completed synchronized interval.

    The input is intentionally an *observed* history rather than an unrestricted
    backtest array.  Supplying any future bar, omitting a supported market, or
    using non-identical event/ATR timelines raises :class:`PolicyError`.
    Insufficient but otherwise causal ATR history produces ``UNKNOWN`` for all
    markets instead of inventing a neutral value.
    """

    if side not in _SIDES:
        raise PolicyError("side must be LONG or SHORT")
    if interval_open_time_ns >= interval_close_time_ns:
        raise PolicyError("ownership interval must have positive duration")
    if interval_close_time_ns > observed_time_ns:
        raise PolicyError("ownership interval is not completed at observation")
    if atr_length <= 0:
        raise PolicyError("atr_length must be positive")
    if campaign_root_id is not None:
        _validate_campaign_root_id(campaign_root_id)

    actual_symbols = set(observed_bars_by_symbol)
    required_symbols = set(SYMBOLS)
    if actual_symbols != required_symbols:
        missing = sorted(required_symbols - actual_symbols)
        extra = sorted(actual_symbols - required_symbols)
        raise PolicyError(f"ownership requires exactly {SYMBOLS}; missing={missing}, extra={extra}")

    histories = {
        symbol: _validate_history(
            symbol=symbol,
            bars=observed_bars_by_symbol[symbol],
            observed_time_ns=observed_time_ns,
        )
        for symbol in SYMBOLS
    }
    event_bars = {
        symbol: tuple(
            bar
            for bar in histories[symbol]
            if bar.open_time_ns >= interval_open_time_ns
            and bar.close_time_ns <= interval_close_time_ns
        )
        for symbol in SYMBOLS
    }
    for symbol, bars in event_bars.items():
        if not bars:
            raise PolicyError(f"{symbol} has no bar in the ownership interval")
        if bars[0].open_time_ns != interval_open_time_ns:
            raise PolicyError(f"{symbol} ownership interval does not start on a bar boundary")
        if bars[-1].close_time_ns != interval_close_time_ns:
            raise PolicyError(f"{symbol} ownership interval is incomplete")
        if bars[0].open <= 0.0 or bars[-1].close <= 0.0:
            raise PolicyError(f"{symbol} ownership interval prices must be positive")

    event_timeline = tuple(
        (bar.open_time_ns, bar.close_time_ns, bar.interval_minutes)
        for bar in event_bars[SYMBOLS[0]]
    )
    for symbol in SYMBOLS[1:]:
        timeline = tuple(
            (bar.open_time_ns, bar.close_time_ns, bar.interval_minutes)
            for bar in event_bars[symbol]
        )
        if timeline != event_timeline:
            raise PolicyError("ownership event bars are not synchronized across all markets")

    prior_samples = {
        symbol: tuple(
            bar for bar in histories[symbol] if bar.close_time_ns <= interval_open_time_ns
        )[-atr_length:]
        for symbol in SYMBOLS
    }
    if all(len(prior_samples[symbol]) == atr_length for symbol in SYMBOLS):
        atr_timeline = tuple(
            (bar.open_time_ns, bar.close_time_ns, bar.interval_minutes)
            for bar in prior_samples[SYMBOLS[0]]
        )
        for symbol in SYMBOLS[1:]:
            timeline = tuple(
                (bar.open_time_ns, bar.close_time_ns, bar.interval_minutes)
                for bar in prior_samples[symbol]
            )
            if timeline != atr_timeline:
                raise PolicyError("pre-interval ATR bars are not synchronized across all markets")

    atrs = {
        symbol: _causal_atr(
            symbol=symbol,
            prior_bars=prior_samples[symbol],
            interval_open_time_ns=interval_open_time_ns,
            atr_length=atr_length,
        )
        for symbol in SYMBOLS
    }
    sign = _SIDES[side]
    raw_returns = {
        symbol: log(event_bars[symbol][-1].close / event_bars[symbol][0].open)
        for symbol in SYMBOLS
    }
    units = {
        symbol: (
            sign * raw_returns[symbol] / atrs[symbol].atr_fraction
            if atrs[symbol].available and atrs[symbol].atr_fraction is not None
            else None
        )
        for symbol in SYMBOLS
    }

    decisions: list[SymbolIntervalOwnership] = []
    for symbol in SYMBOLS:
        role, common, residual = _classify(
            local_units=units[symbol],
            peer_units=tuple(units[peer] for peer in SYMBOLS if peer != symbol),
        )
        decisions.append(
            SymbolIntervalOwnership(
                symbol=symbol,
                side=side,
                interval_open_time_ns=interval_open_time_ns,
                interval_close_time_ns=interval_close_time_ns,
                interval_open=event_bars[symbol][0].open,
                interval_close=event_bars[symbol][-1].close,
                raw_log_return=raw_returns[symbol],
                signed_log_return=sign * raw_returns[symbol],
                atr=atrs[symbol],
                local_delivery_units=units[symbol],
                peer_common_units=common,
                residual_local_units=residual,
                role=role,
            )
        )

    root_id = campaign_root_id or _physical_interval_root_id(
        interval_open_time_ns=interval_open_time_ns,
        interval_close_time_ns=interval_close_time_ns,
        event_bars=event_bars,
    )
    return IntervalOwnershipSnapshot(
        campaign_root_id=root_id,
        ownership_snapshot_id=_ownership_snapshot_id(
            campaign_root_id=root_id,
            side=side,
            interval_open_time_ns=interval_open_time_ns,
            interval_close_time_ns=interval_close_time_ns,
            event_bars=event_bars,
        ),
        common_cascade_id=common_cascade_id(campaign_root_id=root_id),
        side=side,
        interval_open_time_ns=interval_open_time_ns,
        interval_close_time_ns=interval_close_time_ns,
        observed_time_ns=observed_time_ns,
        ownership=tuple(decisions),
    )


__all__ = [
    "ATR_LENGTH",
    "CausalAtrSnapshot",
    "IntervalOwnershipSnapshot",
    "SymbolIntervalOwnership",
    "common_cascade_id",
    "observe_interval_ownership",
    "sibling_episode_id",
    "source_campaign_root_id",
]
