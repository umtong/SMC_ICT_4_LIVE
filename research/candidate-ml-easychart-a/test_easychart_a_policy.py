from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

MODULE = Path(__file__).with_name("easychart_a_policy.py")
spec = importlib.util.spec_from_file_location("easychart_a_policy", MODULE)
policy = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(policy)


def test_public_policy_is_shared_and_one_account() -> None:
    assert policy.POLICY == "EASYCHART_A_CAUSAL_LIQUIDITY_CONTROL_V1"
    assert policy.RISK_FRACTION == 0.03
    assert set(policy.DECISION_MEANINGS) == set(policy.control.SCENARIO_PRIORITY)


def test_state_arbitration_prefers_executable_geometry_without_symbol_rule() -> None:
    base = {
        "research_period": "synthetic",
        "episode_id": "episode-1",
        "state_id": "state-1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "order_time_ns": 1_000,
        "outcome": "TARGET_FIRST",
        "order_terminal_time_ns": 2_000,
        "resolution_time_ns": 3_000,
        "net_r": 2.0,
        "planned_target_net_r": 2.0,
        "approach_impact_per_activity_12m": 1.0,
        "approach_path_efficiency": 0.5,
        "zone_width_bps": 10.0,
        "action_id": "far",
        "entry_geometry": "OTHER",
    }
    proximal = dict(base, action_id="proximal", entry_geometry="ZONE_PROXIMAL_LIMIT")
    selected = policy.choose_public_plans(pd.DataFrame([base, proximal]))
    assert selected.action_id.tolist() == ["proximal"]
    assert selected.policy.tolist() == [policy.POLICY]
