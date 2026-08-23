"""Domain objects for the live liquidity-episode policy.

The module is deliberately free of NautilusTrader imports.  The same objects are
used by historical replay, live paper/shadow, and the optional exchange-testnet
adapter so that market logic does not fork between environments.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
import json
import math
from typing import Any, Iterable, Mapping


SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
RISK_FRACTION = Decimal("0.03")


class PolicyError(ValueError):
    """Raised when a causal or execution contract is violated."""


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise PolicyError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class ContractSpec:
    symbol: str
    tick_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    max_leverage: Decimal = Decimal("20")

    def __post_init__(self) -> None:
        if self.symbol not in SYMBOLS:
            raise PolicyError(f"unsupported symbol: {self.symbol}")
        for name in ("tick_size", "quantity_step", "min_quantity", "min_notional", "max_leverage"):
            if getattr(self, name) <= 0:
                raise PolicyError(f"{name} must be positive")

    def round_price(self, value: float | Decimal) -> Decimal:
        number = Decimal(str(value))
        units = (number / self.tick_size).to_integral_value(rounding=ROUND_DOWN)
        return units * self.tick_size

    def round_quantity(self, value: float | Decimal) -> Decimal:
        number = Decimal(str(value))
        units = (number / self.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        return units * self.quantity_step


DEFAULT_CONTRACTS: dict[str, ContractSpec] = {
    "BTCUSDT": ContractSpec("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("5")),
    "ETHUSDT": ContractSpec("ETHUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("5")),
    "SOLUSDT": ContractSpec("SOLUSDT", Decimal("0.001"), Decimal("0.1"), Decimal("0.1"), Decimal("5")),
    "XRPUSDT": ContractSpec("XRPUSDT", Decimal("0.0001"), Decimal("1"), Decimal("1"), Decimal("5")),
}


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    interval_minutes: int
    open_time_ns: int
    close_time_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    taker_buy_quote_volume: float
    trade_count: int = 0

    def __post_init__(self) -> None:
        if self.symbol not in SYMBOLS:
            raise PolicyError(f"unsupported symbol: {self.symbol}")
        if self.interval_minutes <= 0:
            raise PolicyError("interval_minutes must be positive")
        if self.close_time_ns <= self.open_time_ns:
            raise PolicyError("close_time_ns must be after open_time_ns")
        for name in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote_volume"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.low > min(self.open, self.close, self.high):
            raise PolicyError("bar low is inconsistent")
        if self.high < max(self.open, self.close, self.low):
            raise PolicyError("bar high is inconsistent")
        if self.volume < 0 or self.quote_volume < 0 or self.taker_buy_quote_volume < 0:
            raise PolicyError("volumes must be non-negative")
        if self.trade_count < 0:
            raise PolicyError("trade_count must be non-negative")

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def signed_quote_flow(self) -> float:
        return 2.0 * self.taker_buy_quote_volume - self.quote_volume

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Bar":
        return cls(**dict(payload))


@dataclass(frozen=True, slots=True)
class Pivot:
    pivot_id: str
    symbol: str
    timeframe_minutes: int
    side: str  # HIGH / LOW
    price: float
    event_time_ns: int
    observed_time_ns: int
    serial: int
    strength: float

    def __post_init__(self) -> None:
        if self.side not in {"HIGH", "LOW"}:
            raise PolicyError("pivot side must be HIGH or LOW")
        if self.observed_time_ns < self.event_time_ns:
            raise PolicyError("pivot observation cannot precede event")
        _finite("pivot price", self.price)
        _finite("pivot strength", self.strength)


@dataclass(frozen=True, slots=True)
class LiquidityBoundary:
    boundary_id: str
    symbol: str
    side: str
    kind: str
    timeframe_minutes: int
    observed_time_ns: int
    lower: float
    upper: float
    price: float
    strength: float
    dynamic_slope_per_bar: float = 0.0
    anchor_serial: int = 0
    consumed_time_ns: int | None = None

    def __post_init__(self) -> None:
        if self.side not in {"HIGH", "LOW"}:
            raise PolicyError("boundary side must be HIGH or LOW")
        if self.lower > self.upper:
            raise PolicyError("boundary lower cannot exceed upper")
        if not self.lower <= self.price <= self.upper:
            raise PolicyError("boundary price must lie inside boundary")
        for name in ("lower", "upper", "price", "strength", "dynamic_slope_per_bar"):
            _finite(name, getattr(self, name))

    def price_at(self, serial: int) -> float:
        return self.price + self.dynamic_slope_per_bar * (serial - self.anchor_serial)

    def band_at(self, serial: int) -> tuple[float, float]:
        center = self.price_at(serial)
        half = 0.5 * (self.upper - self.lower)
        return center - half, center + half

    def is_fresh(self, decision_time_ns: int) -> bool:
        return self.observed_time_ns <= decision_time_ns and (
            self.consumed_time_ns is None or self.consumed_time_ns > decision_time_ns
        )


@dataclass(frozen=True, slots=True)
class EntryZone:
    kind: str
    lower: float
    upper: float
    observed_time_ns: int
    source_bar_open_time_ns: int

    def __post_init__(self) -> None:
        if self.lower >= self.upper:
            raise PolicyError("entry zone must have positive width")


@dataclass(frozen=True, slots=True)
class TradePlan:
    episode_id: str
    plan_id: str
    symbol: str
    family: str
    side: str
    decision_time_ns: int
    entry: float
    stop: float
    target: float
    expires_time_ns: int
    source_boundary_id: str
    destination_boundary_id: str
    entry_zone: EntryZone
    evidence: Mapping[str, float | str | int]

    def __post_init__(self) -> None:
        if self.symbol not in SYMBOLS:
            raise PolicyError(f"unsupported symbol: {self.symbol}")
        if self.side not in {"LONG", "SHORT"}:
            raise PolicyError("plan side must be LONG or SHORT")
        if self.family not in {
            "FAILED_AUCTION_REVERSAL",
            "ACCEPTED_AUCTION_CONTINUATION",
            "INITIATIVE_MITIGATION_CONTINUATION",
        }:
            raise PolicyError(f"unknown plan family: {self.family}")
        if self.expires_time_ns <= self.decision_time_ns:
            raise PolicyError("plan expiry must be after decision")
        for name in ("entry", "stop", "target"):
            _finite(name, getattr(self, name))
        if self.side == "LONG" and not (self.stop < self.entry < self.target):
            raise PolicyError("LONG plan must satisfy stop < entry < target")
        if self.side == "SHORT" and not (self.target < self.entry < self.stop):
            raise PolicyError("SHORT plan must satisfy target < entry < stop")
        if self.gross_rr < 1.0 - 1e-12:
            raise PolicyError("gross planned reward/risk must be at least 1.0")
        if self.entry_zone.observed_time_ns > self.decision_time_ns:
            raise PolicyError("entry zone was not observable at decision")

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_distance(self) -> float:
        return abs(self.target - self.entry)

    @property
    def gross_rr(self) -> float:
        return self.reward_distance / self.risk_distance

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["gross_rr"] = self.gross_rr
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TradePlan":
        values = dict(payload)
        values.pop("gross_rr", None)
        values["entry_zone"] = EntryZone(**values["entry_zone"])
        return cls(**values)


@dataclass(frozen=True, slots=True)
class FundingRate:
    symbol: str
    funding_time_ns: int
    rate: float

    def __post_init__(self) -> None:
        if self.symbol not in SYMBOLS:
            raise PolicyError(f"unsupported symbol: {self.symbol}")
        _finite("funding rate", self.rate)


@dataclass(slots=True)
class PendingOrder:
    plan: TradePlan
    quantity: Decimal
    created_time_ns: int
    client_order_id: str


@dataclass(slots=True)
class PositionState:
    plan: TradePlan
    quantity: Decimal
    entry_price: float
    entry_time_ns: int
    entry_fee: float
    funding_paid: float = 0.0


@dataclass(frozen=True, slots=True)
class CompletedTrade:
    plan_id: str
    episode_id: str
    symbol: str
    family: str
    side: str
    entry_time_ns: int
    exit_time_ns: int
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    funding: float
    net_pnl: float
    net_r: float
    planned_gross_rr: float
    holding_minutes: float
    outcome: str
    nav_before: float
    nav_after: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def stable_id(*parts: Any, prefix: str = "") -> str:
    payload = "|".join(str(part) for part in parts)
    digest = sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}{digest}"


def canonical_hash(payload: Mapping[str, Any] | Iterable[Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return sha256(encoded).hexdigest()
