"""Single operational entry point for the causal RE1 automated day trader."""
from easychart_re1_causal_channel import EasyChartRE1CausalChannelBundle
from execution_re1_flow import EasyChartRE1FlowStrategy

EasyChartRE1BotBundle = EasyChartRE1CausalChannelBundle
EasyChartRE1BotStrategy = EasyChartRE1FlowStrategy
MultiScaleScenarioBundle = EasyChartRE1BotBundle
StrategyClass = EasyChartRE1BotStrategy
