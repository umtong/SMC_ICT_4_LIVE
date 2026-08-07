#!/usr/bin/env python3
"""Run candidate-02 v73 inventory/depth states through NautilusTrader."""
from __future__ import annotations
import v53_nt_backtest as runner
from v53_nt_strategy import V53RotationStrategyConfig
from v70_limit_strategy import V70LimitPullbackStrategy
from v73_inventory_depth_core import InventoryDepthConfig,build_rotation_signals,build_state
runner.RotationConfig=InventoryDepthConfig
runner.build_state=build_state
runner.build_rotation_signals=build_rotation_signals
runner.V53RotationStrategy=V70LimitPullbackStrategy
runner.V53RotationStrategyConfig=V53RotationStrategyConfig
if __name__=="__main__":raise SystemExit(runner.main())
