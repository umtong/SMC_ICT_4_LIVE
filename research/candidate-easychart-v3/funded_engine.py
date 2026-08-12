"""NautilusTrader engine factory with historical perpetual funding enabled."""
from __future__ import annotations

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money

from backtest_support import STARTING_NAV, USDT, VENUE
import funding_evidence_timefix  # noqa: F401 - pandas datetime unit repair
from funding_module import HistoricalPerpetualFundingModule


def make_funded_engine(module: HistoricalPerpetualFundingModule) -> BacktestEngine:
    """Build the project engine, adding only Nautilus's simulation-module hook."""
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("EASYCHART-V3-001"),
            logging=LoggingConfig(log_level="ERROR"),
            risk_engine=RiskEngineConfig(bypass=False),
        ),
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(STARTING_NAV, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("100"),
        fill_model=FillModel(prob_fill_on_limit=1.0, prob_slippage=1.0, random_seed=42),
        modules=[module],
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
    )
    return engine
