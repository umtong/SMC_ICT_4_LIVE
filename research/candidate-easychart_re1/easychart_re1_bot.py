"""Single operational entry point for the latent liquidity-episode trader."""
from pathlib import Path
import sys


_RESEARCH = Path(__file__).resolve().parents[1]
for _dependency in (
    _RESEARCH / "candidate-easychart-v2",
    _RESEARCH / "candidate-easychart-v3",
    _RESEARCH / "candidate-easychart-v5",
    _RESEARCH / "candidate-easychart_ml3_breakthrough",
):
    if str(_dependency) not in sys.path:
        sys.path.insert(0, str(_dependency))

from execution_re1_latent_auction import EasyChartRE1LatentAuctionStrategy
from latent_auction import LatentAuctionBundle

EasyChartRE1BotBundle = LatentAuctionBundle
EasyChartRE1BotStrategy = EasyChartRE1LatentAuctionStrategy
MultiScaleScenarioBundle = EasyChartRE1BotBundle
StrategyClass = EasyChartRE1BotStrategy
