"""Stable Nautilus import adapter for Candidate 17 v2.

Candidate 16 owns the legacy top-level module name ``strategy``. Candidate 17
therefore exposes its current implementation through this non-colliding module.
"""
from __future__ import annotations

from remembered_defense_strategy_v2 import Candidate17V2Config as Candidate17Config
from remembered_defense_strategy_v2 import Candidate17V2Strategy as Candidate17Strategy

__all__ = ["Candidate17Config", "Candidate17Strategy"]
