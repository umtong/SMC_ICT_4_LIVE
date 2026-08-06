"""Public facade for candidate 10 tests and research runner."""

import c10_strategy as _strategy_module

from c10_model import BarView
from c10_model import LiquidityPool
from c10_model import MachineParams
from c10_model import Setup
from c10_model import StructuralBar
from c10_model import TradePlan
from c10_model import Transition
from c10_retest_state import AuctionStateMachine

# Candidate10Strategy resolves this module global when on_start runs. Patching it
# here keeps the stable Nautilus execution wrapper while swapping only the
# independently testable market-state implementation.
_strategy_module.AuctionStateMachine = AuctionStateMachine

from c10_current_research import reproducible_weeks
from c10_current_research import run_backtest
from c10_strategy import Candidate10Config
from c10_strategy import Candidate10Strategy
from c10_strategy import make_cost_loaded_btc_perpetual

__all__ = [
    "AuctionStateMachine",
    "BarView",
    "Candidate10Config",
    "Candidate10Strategy",
    "LiquidityPool",
    "MachineParams",
    "Setup",
    "StructuralBar",
    "TradePlan",
    "Transition",
    "make_cost_loaded_btc_perpetual",
    "reproducible_weeks",
    "run_backtest",
]
