"""Production candidate for the causal liquidity-episode trading system.

Execution and account state are owned exclusively by NautilusTrader.  The
package-level API intentionally exposes policy/domain objects only so a second
shadow account implementation cannot be mistaken for the production path.
"""

from .domain import Bar, ContractSpec, DEFAULT_CONTRACTS, FundingRate, TradePlan
from .policy import LiquidityEpisodeCoordinator, PolicyConfig, SymbolEpisodePolicy
from .route_survival import (
    FrozenRoute,
    RouteScore,
    RouteState,
    RouteSurvivalBook,
    native_route_economics,
)

__all__ = [
    "Bar",
    "ContractSpec",
    "DEFAULT_CONTRACTS",
    "FundingRate",
    "FrozenRoute",
    "LiquidityEpisodeCoordinator",
    "PolicyConfig",
    "RouteScore",
    "RouteState",
    "RouteSurvivalBook",
    "SymbolEpisodePolicy",
    "TradePlan",
    "native_route_economics",
]
