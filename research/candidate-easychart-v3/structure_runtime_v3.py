"""Runtime repair and semantic binding for the EasyChart v3 structure-first policy."""
from __future__ import annotations

from typing import Any

from scenario_execution_v5 import ScenarioExecutionMixin


if not getattr(ScenarioExecutionMixin, "_ecv3_return_patch", False):
    _original_advance_footprint_retests = ScenarioExecutionMixin._advance_footprint_retests

    def _advance_footprint_retests_with_result(
        self: Any,
        bar: Any,
        index: int,
    ) -> list[Any]:
        """Return the plans created by the source mixin.

        The reused implementation correctly mutates setup state and appends plans
        to ``self.plans`` but its current source revision omits the final
        ``return output`` statement.  Recover the exact newly-created plans
        without changing any trading decision.
        """
        before = len(self.plans)
        result = _original_advance_footprint_retests(self, bar, index)
        if result is not None:
            return list(result)
        return list(self.plans[before:])

    ScenarioExecutionMixin._advance_footprint_retests = _advance_footprint_retests_with_result
    ScenarioExecutionMixin._ecv3_return_patch = True
