"""Paper strategy compositions for every promotable RE1 execution policy."""
from __future__ import annotations

from execution_re1_venue_context import (
    EasyChartRE1VenueSafeBitcoinContextStrategy,
    EasyChartRE1VenueSafeBreadthStrategy,
    EasyChartRE1VenueSafeFamilyFilterStrategy,
)
from paper_re1_generic import WarmStartCoherentPaperMixin


class EasyChartRE1VenueSafeBreadthPaperStrategy(
    WarmStartCoherentPaperMixin,
    EasyChartRE1VenueSafeBreadthStrategy,
):
    pass


class EasyChartRE1VenueSafeBitcoinContextPaperStrategy(
    WarmStartCoherentPaperMixin,
    EasyChartRE1VenueSafeBitcoinContextStrategy,
):
    pass


class EasyChartRE1VenueSafeFamilyFilterPaperStrategy(
    WarmStartCoherentPaperMixin,
    EasyChartRE1VenueSafeFamilyFilterStrategy,
):
    pass


__all__ = [
    "EasyChartRE1VenueSafeBreadthPaperStrategy",
    "EasyChartRE1VenueSafeBitcoinContextPaperStrategy",
    "EasyChartRE1VenueSafeFamilyFilterPaperStrategy",
]
