"""Causal market observations for source-bound latent owner hypotheses.

One call consumes one *completed*, synchronized five-minute bar for every
configured market.  Normalization statistics are calculated from the prefix
before that bar and the current values are appended only after every source
view has been built.  Thus adding a future suffix cannot alter an observation
already emitted.

The cross-market median is a nuisance observation.  Only the local residual is
used for source-specific impact, so a broad market move cannot by itself look
like evidence for a local owner.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from statistics import median
from typing import Any, Mapping, Sequence

from .latent_owner import OwnerDirection, OwnerIdentity, SourceObservation


DEFAULT_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class OwnerObservationError(ValueError):
    """The synchronized observation contract was violated."""


def _finite(name: str, value: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise OwnerObservationError(f"{name} must be finite")
    return result


def _optional_finite(name: str, value: float | None) -> float | None:
    return None if value is None else _finite(name, value)


@dataclass(frozen=True, slots=True)
class CompletedMarketBar:
    """One completed five-minute perpetual bar plus optional as-of features.

    ``spot_*`` must be supplied as a pair.  Open interest and basis are levels;
    their causal changes are derived against the preceding observed level.
    ``depth_imbalance`` is already a dimensionless contemporaneous observation.
    Missing optional observations remain missing rather than becoming zero.
    """

    symbol: str
    open_time_ns: int
    close_time_ns: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    taker_buy_quote_volume: float
    spot_quote_volume: float | None = None
    spot_taker_buy_quote_volume: float | None = None
    open_interest: float | None = None
    basis: float | None = None
    depth_imbalance: float | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise OwnerObservationError("bar symbol cannot be empty")
        if self.open_time_ns < 0 or self.close_time_ns <= self.open_time_ns:
            raise OwnerObservationError("bar close must follow bar open")
        # This module deliberately accepts timestamps in any epoch convention;
        # duration, rather than timestamp magnitude, establishes the 5m bar.
        if self.close_time_ns - self.open_time_ns != 5 * 60 * 1_000_000_000:
            raise OwnerObservationError("owner observations require completed 5m bars")
        for name in (
            "open",
            "high",
            "low",
            "close",
            "quote_volume",
            "taker_buy_quote_volume",
        ):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        for name in (
            "spot_quote_volume",
            "spot_taker_buy_quote_volume",
            "open_interest",
            "basis",
            "depth_imbalance",
        ):
            object.__setattr__(self, name, _optional_finite(name, getattr(self, name)))
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise OwnerObservationError("inconsistent OHLC bar")
        if self.high < self.low:
            raise OwnerObservationError("bar high cannot be below low")
        if self.close <= 0.0 or self.open <= 0.0:
            raise OwnerObservationError("bar prices must be positive")
        if self.quote_volume < 0.0 or not 0.0 <= self.taker_buy_quote_volume <= self.quote_volume:
            raise OwnerObservationError("invalid perpetual quote volume")
        if (self.spot_quote_volume is None) != (self.spot_taker_buy_quote_volume is None):
            raise OwnerObservationError("spot quote and taker-buy quote must be supplied together")
        if self.spot_quote_volume is not None:
            assert self.spot_taker_buy_quote_volume is not None
            if self.spot_quote_volume < 0.0 or not (
                0.0 <= self.spot_taker_buy_quote_volume <= self.spot_quote_volume
            ):
                raise OwnerObservationError("invalid spot quote volume")
        if self.open_interest is not None and self.open_interest <= 0.0:
            raise OwnerObservationError("open interest must be positive when observed")
        if self.depth_imbalance is not None and not -1.0 <= self.depth_imbalance <= 1.0:
            raise OwnerObservationError("depth imbalance must be in [-1, 1]")

    @property
    def signed_perp_quote(self) -> float:
        return 2.0 * self.taker_buy_quote_volume - self.quote_volume

    @property
    def signed_spot_quote(self) -> float | None:
        if self.spot_quote_volume is None:
            return None
        assert self.spot_taker_buy_quote_volume is not None
        return 2.0 * self.spot_taker_buy_quote_volume - self.spot_quote_volume


@dataclass(frozen=True, slots=True)
class SourceGeometry:
    """Exact source geometry; target is absent until a route signal binds it."""

    identity: OwnerIdentity
    symbol: str
    direction: OwnerDirection
    source_lower: float
    source_upper: float
    target_price: float | None
    attack_reference_price: float

    def __post_init__(self) -> None:
        if not self.symbol:
            raise OwnerObservationError("source symbol cannot be empty")
        if self.direction is not self.identity.direction:
            raise OwnerObservationError("geometry direction must match owner identity")
        for name in ("source_lower", "source_upper", "attack_reference_price"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.target_price is not None:
            object.__setattr__(self, "target_price", _finite("target_price", self.target_price))
        if self.source_lower <= 0.0 or self.source_upper < self.source_lower:
            raise OwnerObservationError("invalid source band")
        sign = 1.0 if self.direction is OwnerDirection.LONG else -1.0
        if self.target_price is not None and sign * (
            self.target_price - self.attack_reference_price
        ) <= 0.0:
            raise OwnerObservationError("target must lie in the owner direction from the attack")


@dataclass(frozen=True, slots=True)
class OwnerObservationConfig:
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    scale_lookback: int = 96
    scale_floor: float = 1e-12

    def __post_init__(self) -> None:
        if len(self.symbols) != 4 or len(set(self.symbols)) != 4 or any(not x for x in self.symbols):
            raise OwnerObservationError("exactly four distinct symbols are required")
        if self.scale_lookback < 2:
            raise OwnerObservationError("scale lookback must be at least two")
        if not math.isfinite(self.scale_floor) or self.scale_floor <= 0.0:
            raise OwnerObservationError("scale floor must be finite and positive")


@dataclass(slots=True)
class _SymbolPrefix:
    close: float | None = None
    open_interest: float | None = None
    open_interest_time_ns: int | None = None
    basis: float | None = None
    basis_time_ns: int | None = None
    returns: list[float] = None  # type: ignore[assignment]
    true_ranges: list[float] = None  # type: ignore[assignment]
    perp_flows: list[float] = None  # type: ignore[assignment]
    spot_flows: list[float] = None  # type: ignore[assignment]
    basis_changes: list[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.returns = [] if self.returns is None else self.returns
        self.true_ranges = [] if self.true_ranges is None else self.true_ranges
        self.perp_flows = [] if self.perp_flows is None else self.perp_flows
        self.spot_flows = [] if self.spot_flows is None else self.spot_flows
        self.basis_changes = [] if self.basis_changes is None else self.basis_changes


def _robust_scale(values: Sequence[float], floor: float) -> float:
    """Causal robust scale using only a supplied prefix.

    Median absolute magnitude remains meaningful for zero-centred signed flow;
    MAD about the median is used when it provides the larger robust dispersion.
    A unit fallback is fixed ex ante and never derived from the current sample.
    """

    if not values:
        return 1.0
    center = median(values)
    median_abs = median(abs(value) for value in values)
    mad = median(abs(value - center) for value in values)
    scale = max(float(median_abs), 1.4826 * float(mad))
    return scale if scale > floor else 1.0


def _trim_append(values: list[float], value: float, lookback: int) -> None:
    values.append(float(value))
    excess = len(values) - lookback
    if excess > 0:
        del values[:excess]


class OwnerObservationBuilder:
    """Stateful, snapshot-safe transform from synchronized bars to source views."""

    SNAPSHOT_VERSION = 2

    def __init__(self, config: OwnerObservationConfig | None = None) -> None:
        self.config = config or OwnerObservationConfig()
        self._prefix = {symbol: _SymbolPrefix() for symbol in self.config.symbols}
        self._last_close_time_ns: int | None = None

    def observe(
        self,
        bars: Mapping[str, CompletedMarketBar],
        geometries: Sequence[SourceGeometry],
    ) -> dict[OwnerIdentity, SourceObservation]:
        """Consume one global completed-bar batch and emit deterministic mappings."""

        expected = set(self.config.symbols)
        if set(bars) != expected:
            raise OwnerObservationError("a global batch must contain every configured symbol once")
        for key, bar in bars.items():
            if key != bar.symbol:
                raise OwnerObservationError("bar mapping key must equal bar symbol")
        close_times = {bar.close_time_ns for bar in bars.values()}
        open_times = {bar.open_time_ns for bar in bars.values()}
        if len(close_times) != 1 or len(open_times) != 1:
            raise OwnerObservationError("global bars must be synchronized")
        close_time_ns = next(iter(close_times))
        if self._last_close_time_ns is not None and close_time_ns <= self._last_close_time_ns:
            raise OwnerObservationError("completed global bars must advance causally")

        raw_return: dict[str, float | None] = {}
        normalized_return: dict[str, float | None] = {}
        perp_flow: dict[str, float] = {}
        spot_flow: dict[str, float | None] = {}
        true_range: dict[str, float] = {}
        atr_scale: dict[str, float] = {}
        oi_change: dict[str, float | None] = {}
        basis_change: dict[str, float | None] = {}
        depth: dict[str, float | None] = {}

        # Every scale below is frozen from the prior prefix.  Nothing from this
        # bar is appended until all identity views have been constructed.
        for symbol in self.config.symbols:
            bar = bars[symbol]
            prefix = self._prefix[symbol]
            raw = None if prefix.close is None else math.log(bar.close / prefix.close)
            raw_return[symbol] = raw
            normalized_return[symbol] = (
                None
                if raw is None
                else raw / _robust_scale(prefix.returns, self.config.scale_floor)
            )
            perp_flow[symbol] = bar.signed_perp_quote / _robust_scale(
                prefix.perp_flows, self.config.scale_floor
            )
            signed_spot = bar.signed_spot_quote
            spot_flow[symbol] = (
                None
                if signed_spot is None
                else signed_spot / _robust_scale(prefix.spot_flows, self.config.scale_floor)
            )
            previous_close = bar.open if prefix.close is None else prefix.close
            true_range[symbol] = max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
            atr_scale[symbol] = _robust_scale(prefix.true_ranges, self.config.scale_floor)
            oi_change[symbol] = (
                None
                if bar.open_interest is None
                or prefix.open_interest is None
                or prefix.open_interest_time_ns != bar.open_time_ns
                else (bar.open_interest - prefix.open_interest) / prefix.open_interest
            )
            raw_basis_change = (
                None
                if bar.basis is None
                or prefix.basis is None
                or prefix.basis_time_ns != bar.open_time_ns
                else bar.basis - prefix.basis
            )
            basis_change[symbol] = (
                None
                if raw_basis_change is None
                else raw_basis_change
                / _robust_scale(prefix.basis_changes, self.config.scale_floor)
            )
            depth[symbol] = bar.depth_imbalance

        available_returns = [
            float(normalized_return[symbol])
            for symbol in self.config.symbols
            if normalized_return[symbol] is not None
        ]
        common = median(available_returns) if len(available_returns) == len(self.config.symbols) else None

        by_identity: dict[OwnerIdentity, SourceObservation] = {}
        for geometry in sorted(
            geometries,
            key=lambda item: (item.identity.token, item.symbol),
        ):
            if geometry.symbol not in expected:
                raise OwnerObservationError("source geometry has an unconfigured symbol")
            if geometry.identity in by_identity:
                raise OwnerObservationError("duplicate source owner identity in one global bar")
            symbol = geometry.symbol
            bar = bars[symbol]
            sign = 1.0 if geometry.direction is OwnerDirection.LONG else -1.0
            local = (
                None
                if common is None or normalized_return[symbol] is None
                else float(normalized_return[symbol]) - common
            )
            source_center = (geometry.source_lower + geometry.source_upper) / 2.0
            target_risk = (
                None
                if geometry.target_price is None
                else abs(geometry.target_price - geometry.attack_reference_price)
            )
            source_width = geometry.source_upper - geometry.source_lower
            geometry_unit = max(
                source_width,
                atr_scale[symbol],
                self.config.scale_floor,
            )
            aligned_residual = None if local is None else sign * local
            flow_magnitude = abs(perp_flow[symbol])
            impact = (
                None
                if aligned_residual is None or flow_magnitude <= self.config.scale_floor
                else aligned_residual / flow_magnitude
            )
            kwargs: dict[str, float | int | None] = {
                "time_ns": close_time_ns,
                "return_progress": (
                    None if normalized_return[symbol] is None else sign * normalized_return[symbol]
                ),
                "source_progress": sign
                * (bar.close - geometry.attack_reference_price)
                / geometry_unit,
                "spot_flow": None if spot_flow[symbol] is None else sign * spot_flow[symbol],
                "perp_flow": sign * perp_flow[symbol],
                "impact_per_flow": impact,
                "distance_from_source": sign * (bar.close - source_center) / geometry_unit,
                "target_progress": (
                    None
                    if target_risk is None
                    else sign * (bar.close - geometry.attack_reference_price) / target_risk
                ),
                # Conditioning-only diagnostic: broad market direction must
                # not become evidence for either source owner direction.
                "common_nuisance": common,
                "residual_return": aligned_residual,
                # OI is deliberately not owner-aligned: it remains an anonymous
                # raw market-state change.  The other two are coordinate views.
                "open_interest_change": oi_change[symbol],
                "basis_change": (
                    None if basis_change[symbol] is None else sign * basis_change[symbol]
                ),
                "depth_imbalance": None if depth[symbol] is None else sign * depth[symbol],
            }
            by_identity[geometry.identity] = SourceObservation(**kwargs)

        for symbol in self.config.symbols:
            bar = bars[symbol]
            prefix = self._prefix[symbol]
            if raw_return[symbol] is not None:
                _trim_append(prefix.returns, float(raw_return[symbol]), self.config.scale_lookback)
            _trim_append(prefix.true_ranges, true_range[symbol], self.config.scale_lookback)
            _trim_append(prefix.perp_flows, bar.signed_perp_quote, self.config.scale_lookback)
            if bar.signed_spot_quote is not None:
                _trim_append(prefix.spot_flows, bar.signed_spot_quote, self.config.scale_lookback)
            if bar.basis is not None and prefix.basis is not None:
                _trim_append(
                    prefix.basis_changes,
                    bar.basis - prefix.basis,
                    self.config.scale_lookback,
                )
            prefix.close = bar.close
            if bar.open_interest is not None:
                prefix.open_interest = bar.open_interest
                prefix.open_interest_time_ns = bar.close_time_ns
            if bar.basis is not None:
                prefix.basis = bar.basis
                prefix.basis_time_ns = bar.close_time_ns
        self._last_close_time_ns = close_time_ns
        return by_identity

    def export_state(self) -> dict[str, Any]:
        return {
            "version": self.SNAPSHOT_VERSION,
            "symbols": list(self.config.symbols),
            "scale_lookback": self.config.scale_lookback,
            "scale_floor": self.config.scale_floor,
            "last_close_time_ns": self._last_close_time_ns,
            "prefix": {
                symbol: {
                    "close": state.close,
                    "open_interest": state.open_interest,
                    "open_interest_time_ns": state.open_interest_time_ns,
                    "basis": state.basis,
                    "basis_time_ns": state.basis_time_ns,
                    "returns": list(state.returns),
                    "true_ranges": list(state.true_ranges),
                    "perp_flows": list(state.perp_flows),
                    "spot_flows": list(state.spot_flows),
                    "basis_changes": list(state.basis_changes),
                }
                for symbol, state in sorted(self._prefix.items())
            },
        }

    def restore_state(self, snapshot: Mapping[str, Any]) -> None:
        if int(snapshot.get("version", -1)) != self.SNAPSHOT_VERSION:
            raise OwnerObservationError("unsupported owner-observation snapshot version")
        if tuple(snapshot.get("symbols", ())) != self.config.symbols:
            raise OwnerObservationError("snapshot symbols do not match configuration")
        if int(snapshot.get("scale_lookback", -1)) != self.config.scale_lookback:
            raise OwnerObservationError("snapshot lookback does not match configuration")
        if float(snapshot.get("scale_floor", -1.0)) != self.config.scale_floor:
            raise OwnerObservationError("snapshot scale floor does not match configuration")
        raw_prefix = snapshot.get("prefix")
        if not isinstance(raw_prefix, Mapping) or set(raw_prefix) != set(self.config.symbols):
            raise OwnerObservationError("snapshot prefix does not match configured symbols")
        restored: dict[str, _SymbolPrefix] = {}
        for symbol in self.config.symbols:
            raw = raw_prefix[symbol]
            if not isinstance(raw, Mapping):
                raise OwnerObservationError("invalid symbol prefix snapshot")
            lists: dict[str, list[float]] = {}
            for name in ("returns", "true_ranges", "perp_flows", "spot_flows", "basis_changes"):
                value = [_finite(name, item) for item in raw.get(name, ())]
                if len(value) > self.config.scale_lookback:
                    raise OwnerObservationError("snapshot history exceeds configured lookback")
                lists[name] = value
            restored[symbol] = _SymbolPrefix(
                close=_optional_finite("close", raw.get("close")),
                open_interest=_optional_finite("open_interest", raw.get("open_interest")),
                open_interest_time_ns=(
                    None
                    if raw.get("open_interest_time_ns") is None
                    else int(raw["open_interest_time_ns"])
                ),
                basis=_optional_finite("basis", raw.get("basis")),
                basis_time_ns=(
                    None if raw.get("basis_time_ns") is None else int(raw["basis_time_ns"])
                ),
                returns=lists["returns"],
                true_ranges=lists["true_ranges"],
                perp_flows=lists["perp_flows"],
                spot_flows=lists["spot_flows"],
                basis_changes=lists["basis_changes"],
            )
        last = snapshot.get("last_close_time_ns")
        self._prefix = restored
        self._last_close_time_ns = None if last is None else int(last)

    @classmethod
    def from_state(
        cls,
        snapshot: Mapping[str, Any],
        config: OwnerObservationConfig | None = None,
    ) -> "OwnerObservationBuilder":
        result = cls(config)
        result.restore_state(snapshot)
        return result

    def canonical_snapshot(self) -> str:
        return json.dumps(self.export_state(), sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = [
    "CompletedMarketBar",
    "DEFAULT_SYMBOLS",
    "OwnerObservationBuilder",
    "OwnerObservationConfig",
    "OwnerObservationError",
    "SourceGeometry",
]
