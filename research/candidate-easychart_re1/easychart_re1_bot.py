"""Canonical EasyChart RE1 automated day-trading policy.

This is the single operational entry point for the complete auction policy:
responsible rejection, event-local OB/FVG continuation, horizontal S/R flip,
contextual local efficient pullback, residual macro-trend pullback and residual
mature diagonal acceptance.  All continuation mechanisms share the same broad
context responsibility and compete for one global account position.
"""
from easychart_re1_complete_bot_policy_v2 import EasyChartRE1CompleteBotPolicyV2Bundle
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy

EasyChartRE1BotBundle = EasyChartRE1CompleteBotPolicyV2Bundle
EasyChartRE1BotStrategy = EasyChartRE1LocalAuctionStrategy
MultiScaleScenarioBundle = EasyChartRE1BotBundle
StrategyClass = EasyChartRE1BotStrategy
