"""Explicit strategy binding for the EasyChart v3 research scenario bundle."""
from __future__ import annotations

import mtf_strategy as _base
from scenario_bundle_v3 import ResearchScenarioBundle

_base.MultiScaleScenarioBundle = ResearchScenarioBundle
EasyChartMTFConfig = _base.EasyChartMTFConfig


class EasyChartMTFStrategy(_base.EasyChartMTFStrategy):
    pass
