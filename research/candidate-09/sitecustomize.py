"""Narrow NautilusTrader import compatibility for direct script execution.

NautilusTrader releases have exposed BacktestEngineConfig from either the common
config module or the backtest engine module.  Candidate code intentionally imports
the latter; this startup hook preserves that stable import without changing any
engine behavior.  It is loaded only when Python starts with this candidate directory
as the script path.
"""

try:
    import nautilus_trader.backtest.engine as _engine
    from nautilus_trader.config import BacktestEngineConfig as _BacktestEngineConfig

    if not hasattr(_engine, "BacktestEngineConfig"):
        _engine.BacktestEngineConfig = _BacktestEngineConfig
except Exception:
    # The actual import error remains visible when run.py imports NautilusTrader.
    pass
