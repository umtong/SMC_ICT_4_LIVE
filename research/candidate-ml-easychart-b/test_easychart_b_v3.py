from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import harvest_structural as harvest
import easychart_b_v3 as router


def frame(highs, lows=None, closes=None):
    lows = lows if lows is not None else highs
    closes = closes if closes is not None else highs
    return pd.DataFrame(
        {"high": highs, "low": lows, "close": closes},
        index=pd.date_range("2025-01-01", periods=len(highs), freq="min", tz="UTC"),
    )


def candidate(side="LONG"):
    setup = SimpleNamespace(
        side=side,
        setup_kind="TEST",
        upper=100.0,
        lower=99.0,
    )
    return SimpleNamespace(setup=setup, departure_index=0)


def obstacle(price, kind="TEST_ROUTE"):
    return harvest.core.v4.Obstacle(
        obstacle_id=f"O:{price}",
        kind=kind,
        timeframe_minutes=15,
        structure_price=price,
        order_price=price,
        strength=1.0,
        source_level_id="L",
    )


def economics(_side, entry, _stop, target, _tick):
    return {"target_net_r": abs(target - entry), "stop_net_r": -1.0}


def test_impulse_reclaim_precedes_far_route(monkeypatch):
    monkeypatch.setattr(harvest.core, "_raw_economics", economics)
    monkeypatch.setattr(
        harvest.core.v4,
        "_first_obstacle",
        lambda *_args, **_kwargs: (obstacle(105.0), {}),
    )
    plan = harvest._structural_target_at_arm(
        frame([100.0, 102.1], [99.8, 101.0], [100.0, 101.8]),
        [], {}, candidate(), 1, 100.0, 99.0, 0.1,
    )
    assert plan is not None
    _, features, target, gross_rr, _, provenance = plan
    assert provenance == "IMPULSE_RECLAIM"
    assert target == 102.0
    assert gross_rr == 2.0
    assert features["opposing_live_frontier_price"] == 105.0


def test_route_is_used_when_impulse_has_not_yet_paid_one_r(monkeypatch):
    monkeypatch.setattr(harvest.core, "_raw_economics", economics)
    monkeypatch.setattr(
        harvest.core.v4,
        "_first_obstacle",
        lambda *_args, **_kwargs: (obstacle(102.0), {}),
    )
    plan = harvest._structural_target_at_arm(
        frame([100.0, 100.7], [99.8, 100.2], [100.0, 100.5]),
        [], {}, candidate(), 1, 100.0, 99.0, 0.1,
    )
    assert plan is not None
    assert plan[-1] == "OPPOSING_LIVE_FRONTIER"
    assert plan[2] == 102.0


def test_already_traversed_route_is_not_called_live(monkeypatch):
    monkeypatch.setattr(harvest.core, "_raw_economics", economics)
    monkeypatch.setattr(
        harvest.core.v4,
        "_first_obstacle",
        lambda *_args, **_kwargs: (obstacle(101.5), {}),
    )
    plan = harvest._structural_target_at_arm(
        frame([100.0, 103.1], [99.8, 102.0], [100.0, 102.8]),
        [], {}, candidate(), 1, 100.0, 99.0, 0.1,
    )
    assert plan is not None
    assert plan[-1] == "IMPULSE_RECLAIM"
    assert plan[2] == 103.0


def test_natural_three_r_target_is_not_capped(monkeypatch):
    monkeypatch.setattr(harvest.core, "_raw_economics", economics)
    monkeypatch.setattr(
        harvest.core.v4,
        "_first_obstacle",
        lambda *_args, **_kwargs: (obstacle(106.0), {}),
    )
    plan = harvest._structural_target_at_arm(
        frame([100.0, 103.1], [99.8, 102.0], [100.0, 102.8]),
        [], {}, candidate(), 1, 100.0, 99.0, 0.1,
    )
    assert plan is not None
    assert plan[3] == 3.0


def test_router_uses_revealed_but_not_consumed_control_state():
    row = {
        "family": "FAILED_AUCTION_REVERSAL",
        "auction_phase": "ACCEPTED_EXPANSION",
        "arm_outside_close_ratio": 0.7,
        "arm_outside_volume_share": 0.8,
        "arm_path_efficiency": 0.3,
        "arm_current_retrace_fraction": 0.1,
        "arm_activity_ratio": 1.2,
        "arm_structural_target_consumed_fraction": 0.45,
        "arm_futures_index_residual_signed": 0.001,
        "departure_residual_return_3m_signed": -0.001,
        "source_defense_count": 1,
    }
    masks = router.scenario_masks(pd.DataFrame([row]))
    assert bool(masks["FAILED_AUCTION_LOCAL_RECLAIM"].iloc[0])
    late = dict(row, arm_structural_target_consumed_fraction=0.95)
    masks = router.scenario_masks(pd.DataFrame([late]))
    assert not bool(masks["FAILED_AUCTION_LOCAL_RECLAIM"].iloc[0])
