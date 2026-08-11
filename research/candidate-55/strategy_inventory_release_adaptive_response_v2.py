"""Runtime-contract repair for Candidate 55 adaptive-liquidity response.

The V1 policy was not economically falsified.  Every matrix run stopped when
its valid adaptive response reached the inherited global arbitration code:
``strategy_inventory_release_second_touch.Candidate35Strategy`` sorts response
records by ``target_net_r``, ``directional_flow_15s`` and
``directional_depth``.  V1 returned an otherwise valid record without those
three arbitration fields, producing ``KeyError('target_net_r')`` before an
order could be submitted.

This module changes no market-state boundary, context rule, stop, target,
holding rule, cost model, risk sizing or single-slot arbitration.  It only
adapts the V1 response record to the established arbitration interface using
values already available at the same completed response minute:

* ``target_net_r`` is the frozen modeled net target R from the V1 target
  geometry (falling back to the frozen config value only if the diagnostic
  alias is absent);
* ``directional_flow_15s`` is the response-side signed 15-second aggressor
  flow;
* ``directional_depth`` is the response-side signed level-1 depth imbalance.

The helper is deliberately pure so the workflow can unit-check the interface
before spending time on data preparation and NautilusTrader execution.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Any, MutableMapping

from router import RouteDecision


_V1_PATH = (
    Path(__file__).resolve().parents[1]
    / "candidate-55"
    / "strategy_inventory_release_adaptive_response.py"
)
_V1_SPEC = importlib.util.spec_from_file_location(
    "candidate55_inventory_release_adaptive_response_v1_for_v2",
    _V1_PATH,
)
if _V1_SPEC is None or _V1_SPEC.loader is None:
    raise RuntimeError(f"cannot load adaptive-response v1 policy: {_V1_PATH}")
_V1 = importlib.util.module_from_spec(_V1_SPEC)
sys.modules[_V1_SPEC.name] = _V1
_V1_SPEC.loader.exec_module(_V1)


Candidate35Config = _V1.Candidate35Config
_cost_after_net_r = _V1._cost_after_net_r


def _patch_response_sort_record(
    *,
    record: MutableMapping[str, Any],
    decision_diagnostics: MutableMapping[str, Any] | dict[str, Any],
    side: int,
    observation: Any,
    fallback_target_net_r: float,
) -> MutableMapping[str, Any]:
    """Populate the inherited second-touch arbitration contract in-place."""
    if int(side) not in (-1, 1):
        raise ValueError(f"side must be -1 or 1, received {side}")
    target_net_r = float(
        decision_diagnostics.get("modeled_net_target_r", fallback_target_net_r)
    )
    directional_flow_15s = int(side) * float(observation.flow_15s)
    directional_depth = int(side) * float(observation.depth_imbalance_1)
    values = (target_net_r, directional_flow_15s, directional_depth)
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError(
            "adaptive response arbitration received non-finite values: "
            f"target_net_r={target_net_r}, "
            f"directional_flow_15s={directional_flow_15s}, "
            f"directional_depth={directional_depth}"
        )
    record.update(
        {
            "target_net_r": target_net_r,
            "directional_flow_15s": directional_flow_15s,
            "directional_depth": directional_depth,
        }
    )
    return record


class Candidate35Strategy(_V1.Candidate35Strategy):
    """The frozen V1 policy with its response/arbitration interface repaired."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate": "candidate-55-inventory-release-adaptive-response-v2",
                "adaptive_response_arbitration_contract": (
                    "target_net_r + signed flow_15s + signed depth_imbalance_1"
                ),
                "adaptive_response_arbitration_contract_repairs": 0,
            }
        )

    def _response_candidates(
        self,
        ts_event: int,
        observations: dict[str, Any],
    ) -> list[tuple[RouteDecision, tuple[str, int, int], dict[str, float]]]:
        responses = super()._response_candidates(ts_event, observations)
        for decision, key, record in responses:
            symbol = str(key[0])
            observation = observations.get(symbol)
            if observation is None or not observation.ready:
                raise RuntimeError(
                    "V1 returned an adaptive response without a ready observation "
                    f"for {symbol} at {ts_event}"
                )
            _patch_response_sort_record(
                record=record,
                decision_diagnostics=decision.diagnostics,
                side=int(decision.side),
                observation=observation,
                fallback_target_net_r=float(self.config.inventory_target_net_r),
            )
            self.diagnostics[
                "adaptive_response_arbitration_contract_repairs"
            ] += 1
        return responses


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_cost_after_net_r",
    "_patch_response_sort_record",
]
