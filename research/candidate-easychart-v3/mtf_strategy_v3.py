"""Explicit strategy binding for the EasyChart v3 research scenario bundle."""
from __future__ import annotations

import mtf_strategy as _base
from horizontal_structure_v3 import StrongResearchScenarioBundle

_base.MultiScaleScenarioBundle = StrongResearchScenarioBundle
EasyChartMTFConfig = _base.EasyChartMTFConfig


class EasyChartMTFStrategy(_base.EasyChartMTFStrategy):
    pass
