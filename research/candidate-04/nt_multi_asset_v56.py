#!/usr/bin/env python3
"""Four-asset V56 execution through the trusted one-account Nautilus runner."""
from __future__ import annotations

from typing import Any

from nautilus_trader.config import ImportableStrategyConfig

import nt_multi_asset_rich_backtest as runner
import nt_multi_asset_rich_backtest_v44 as v44


_ORIGINAL_STRATEGY_CONFIG = runner.strategy_config


def v56_strategy_config(
    symbol: str,
    config: dict[str, Any],
    signals_root: Any,
    strategy_root: Any,
    coordinator_key: str,
) -> ImportableStrategyConfig:
    imported = _ORIGINAL_STRATEGY_CONFIG(
        symbol,
        config,
        signals_root,
        strategy_root,
        coordinator_key,
    )
    return ImportableStrategyConfig(
        strategy_path=(
            "nt_global_state_preserving_rich_signal_strategy:"
            "GlobalStatePreservingRichSignalStrategy"
        ),
        config_path=(
            "nt_global_rich_signal_strategy:GlobalRichSignalConfig"
        ),
        config=dict(imported.config),
    )


runner.strategy_config = v56_strategy_config


if __name__ == "__main__":
    v44.base.main()
