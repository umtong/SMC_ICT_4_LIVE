"""Research binding for the current learned-horizontal campaign ablation.

Python imports ``sitecustomize`` automatically from this research PYTHONPATH.
The binding changes only which learned-horizontal engine the existing scenario
bundle instantiates; Nautilus execution, funding, account, generic structure
engines and the global one-position router are unchanged.
"""
from __future__ import annotations

import scenario_bundle_v5 as _bundle
from learned_horizontal_campaign_v8 import (
    CampaignLearnedHorizontalScenarioEngine,
)


_bundle.ConfirmationCloseLearnedHorizontalScenarioEngine = (
    CampaignLearnedHorizontalScenarioEngine
)
