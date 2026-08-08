"""Candidate 09 v44: frozen V42 POC migration on four markets, one account.

Every symbol uses the same completed-auction price state, consecutive outside
footprint-POC ownership, first defended retest, boundary invalidation, natural
objective, costs and current-whole-account 3% planned loss.  Symbol differences
are limited to frozen exchange tick and quantity contracts.  One audited global
slot serializes every new entry across BTC, ETH, SOL and XRP.
"""
from __future__ import annotations

from portfolio_strategy_v40 import SharedSlotMixin
from strategy_v42 import Candidate16Config
from strategy_v42 import Candidate16Strategy as _Candidate42Strategy


class SharedAccountV44BTCStrategy(SharedSlotMixin, _Candidate42Strategy):
    pass


class SharedAccountV44ETHStrategy(SharedSlotMixin, _Candidate42Strategy):
    pass


class SharedAccountV44SOLStrategy(SharedSlotMixin, _Candidate42Strategy):
    pass


class SharedAccountV44XRPStrategy(SharedSlotMixin, _Candidate42Strategy):
    pass


STRATEGY_PATHS = {
    "BTCUSDT": "portfolio_strategy:SharedAccountV44BTCStrategy",
    "ETHUSDT": "portfolio_strategy:SharedAccountV44ETHStrategy",
    "SOLUSDT": "portfolio_strategy:SharedAccountV44SOLStrategy",
    "XRPUSDT": "portfolio_strategy:SharedAccountV44XRPStrategy",
}


def reset_shared_btc_leader_context() -> None:
    """Compatibility no-op for the reused process-isolated shared runner."""


__all__ = [
    "Candidate16Config",
    "SharedAccountV44BTCStrategy",
    "SharedAccountV44ETHStrategy",
    "SharedAccountV44SOLStrategy",
    "SharedAccountV44XRPStrategy",
    "STRATEGY_PATHS",
    "reset_shared_btc_leader_context",
]
