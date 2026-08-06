"""Public facade for candidate 10 tests and research runner."""

from c10_model import AuctionRange
from c10_model import BarView
from c10_model import MachineParams
from c10_model import Setup
from c10_model import TradePlan
from c10_model import Transition
from c10_research import reproducible_weeks
from c10_research import run_backtest
from c10_state import AuctionStateMachine
from c10_strategy import Candidate10Config
from c10_strategy import Candidate10Strategy
from c10_strategy import make_cost_loaded_btc_perpetual

__all__ = [
    "AuctionRange",
    "AuctionStateMachine",
    "BarView",
    "Candidate10Config",
    "Candidate10Strategy",
    "MachineParams",
    "Setup",
    "TradePlan",
    "Transition",
    "make_cost_loaded_btc_perpetual",
    "reproducible_weeks",
    "run_backtest",
]
