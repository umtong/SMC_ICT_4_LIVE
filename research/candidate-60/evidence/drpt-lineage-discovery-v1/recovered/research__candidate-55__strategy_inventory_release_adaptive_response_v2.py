"""Runtime-contract repair for Candidate 55 adaptive-liquidity response.

The V1 policy was not economically falsified.  Its valid responses failed at
runtime because the inherited second-touch controller consumes a response
record contract which V1 did not implement completely.  The controller uses
three fields for global arbitration and two more fields when it freezes the
selected scenario:

* ``target_net_r``;
* ``directional_flow_15s``;
* ``directional_depth``;
* ``reference``;
* ``target``.

This module changes no market-state boundary, context rule, stop, target,
holding rule, cost model, risk sizing or single-slot arbitration.  It adapts
the V1 response to that established interface using only information available
at the same completed response minute.  It also restores adaptive-policy
provenance after the inherited controller labels the selected scenario as its
second-touch parent.

The pure helper is unit-checked before each expensive exact replay so interface
regressions fail before data preparation or NautilusTrader execution.
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


_RESPONSE_CONTRACT_KEYS = (
    "target_net_r",
    "directional_flow_15s",
    "directional_depth",
    "reference",
    "target",
)


def _patch_response_record_contract(
    *,
    record: MutableMapping[str, Any],
    decision_diagnostics: MutableMapping[str, Any] | dict[str, Any],
    side: int,
    observation: Any,
    reference: float,
    fallback_target_net_r: float,
) -> MutableMapping[str, Any]:
    """Populate the complete inherited response-consumer contract in-place."""
    if int(side) not in (-1, 1):
        raise ValueError(f"side must be -1 or 1, received {side}")
    if "target" not in record:
        raise RuntimeError("adaptive response record has no frozen target")

    target_net_r = float(
        decision_diagnostics.get("modeled_net_target_r", fallback_target_net_r)
    )
    directional_flow_15s = int(side) * float(observation.flow_15s)
    directional_depth = int(side) * float(observation.depth_imbalance_1)
    reference_value = float(reference)
    target_value = float(record["target"])
    values = (
        target_net_r,
        directional_flow_15s,
        directional_depth,
        reference_value,
        target_value,
    )
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError(
            "adaptive response consumer contract received non-finite values: "
            f"target_net_r={target_net_r}, "
            f"directional_flow_15s={directional_flow_15s}, "
            f"directional_depth={directional_depth}, "
            f"reference={reference_value}, target={target_value}"
        )
    record.update(
        {
            "target_net_r": target_net_r,
            "directional_flow_15s": directional_flow_15s,
            "directional_depth": directional_depth,
            "reference": reference_value,
            "target": target_value,
        }
    )
    missing = [key for key in _RESPONSE_CONTRACT_KEYS if key not in record]
    if missing:
        raise RuntimeError(f"adaptive response contract still missing {missing}")
    return record


# Backward-compatible alias retained for the first repair workflow manifest.
_patch_response_sort_record = _patch_response_record_contract


class Candidate35Strategy(_V1.Candidate35Strategy):
    """The frozen V1 policy with its full consumer interface repaired."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate": "candidate-55-inventory-release-adaptive-response-v2",
                "adaptive_response_consumer_contract": list(
                    _RESPONSE_CONTRACT_KEYS
                ),
                "adaptive_response_arbitration_contract_repairs": 0,
                "adaptive_response_selected_provenance_repairs": 0,
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
            arm = self.armed_second_touches.get(key)
            if observation is None or not observation.ready:
                raise RuntimeError(
                    "V1 returned an adaptive response without a ready observation "
                    f"for {symbol} at {ts_event}"
                )
            if arm is None:
                raise RuntimeError(
                    "V1 returned an adaptive response without its causal arm "
                    f"for {key} at {ts_event}"
                )
            _patch_response_record_contract(
                record=record,
                decision_diagnostics=decision.diagnostics,
                side=int(decision.side),
                observation=observation,
                reference=float(arm.reference),
                fallback_target_net_r=float(self.config.inventory_target_net_r),
            )
            self.diagnostics[
                "adaptive_response_arbitration_contract_repairs"
            ] += 1
        return responses

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        entries_before = int(self.diagnostics.get("second_touch_entries", 0))
        super()._on_complete_universe_minute(ts_event)
        entries_after = int(self.diagnostics.get("second_touch_entries", 0))
        if entries_after <= entries_before or self.current_scenario is None:
            return
        self.current_scenario.update(
            {
                "candidate": (
                    "candidate-55-inventory-release-adaptive-response-v2"
                ),
                "state_model": (
                    "inventory release + premium turn -> real-flow "
                    "sweep/reclaim -> first completed minute liquidity adaptation"
                ),
                "risk_geometry": (
                    "frozen absorption-interaction extreme plus 0.15 ATR"
                ),
                "management": "cost-after +2R objective or 240-minute timeout",
            }
        )
        self.diagnostics[
            "adaptive_response_selected_provenance_repairs"
        ] += 1


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_RESPONSE_CONTRACT_KEYS",
    "_cost_after_net_r",
    "_patch_response_record_contract",
    "_patch_response_sort_record",
]
