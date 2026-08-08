"""Import adapter for Candidate 17's uniquely named strategy module.

Candidate 16 v2 imports ``Candidate16Config`` from a top-level module literally
named ``strategy``. Candidate 17 therefore keeps its implementation under the
non-colliding ``remembered_defense_strategy`` filename and exposes the classes
expected by NautilusTrader through this adapter.
"""
from __future__ import annotations

from remembered_defense_strategy import Candidate17Config
from remembered_defense_strategy import Candidate17Strategy

__all__ = ["Candidate17Config", "Candidate17Strategy"]
