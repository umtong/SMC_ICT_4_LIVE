"""Canonical public aliases for the EasyChart RE1 ML3 system."""
from easychart_re1_complete_bot_policy_v2 import EasyChartRE1CompleteBotPolicyV2Bundle
from execution_re1_ml3 import EasyChartRE1ML3Strategy


EasyChartRE1ML3Bundle = EasyChartRE1CompleteBotPolicyV2Bundle
MultiScaleScenarioBundle = EasyChartRE1ML3Bundle
StrategyClass = EasyChartRE1ML3Strategy


__all__ = [
    "EasyChartRE1ML3Bundle",
    "EasyChartRE1ML3Strategy",
    "MultiScaleScenarioBundle",
    "StrategyClass",
]
