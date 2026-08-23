from __future__ import annotations

import math

import pandas as pd

import candidate_3b_logic as logic
import candidate_3b_policy as policy


def _row(
    action: str,
    state: str,
    episode: str,
    order: int,
    terminal: int,
    *,
    period: str = "test",
    rank: int = 0,
    tier: float = 1.2,
    target_net: float = 0.4,
    proof: float = 0.4,
    filled: bool = True,
    net_r: float = 0.4,
) -> dict:
    return {
        "period": period,
        "action_id": action,
        "state_id": state,
        "episode_id": episode,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "scenario_family": "PROVEN" if rank else "CONFLUENT",
        "scenario_rank": rank,
        "target_tier_r": tier,
        "planned_target_net_r": target_net,
        "gross_rr": tier,
        "route_rr": 8.0,
        "route_utilization": tier / 8.0,
        "proof_margin_r": proof,
        "auction_best_progress_r": tier + proof,
        "auction_progress_r": tier,
        "auction_effort_result": 2.5,
        "auction_acceptance_strength": 1.2,
        "order_time_ns": order,
        "order_terminal_time_ns": terminal,
        "fill_time_ns": order + 1 if filled else None,
        "resolution_time_ns": terminal if filled else None,
        "fill_state": "FILLED_LIMIT" if filled else "EXPIRED_UNFILLED",
        "outcome": "TARGET_FIRST" if filled and net_r > 0 else ("STOP_FIRST" if filled else "UNFILLED"),
        "net_r": net_r if filled else None,
        "holding_minutes": 10.0 if filled else None,
    }


def test_evidence_tiers_reject_unproven_or_failed_auction():
    failed = logic.evidence_tiers(
        family="FAILED_AUCTION_REVERSAL",
        location_kind="BOUNDARY_FVG_OVERLAP",
        auction_phase=logic.FIRST_RETEST,
        best_progress_r=5.0,
        effort_result=5.0,
        route_rr=20.0,
    )
    assert tuple(failed) == ()

    weak = logic.evidence_tiers(
        family=logic.ACCEPTED,
        location_kind="BOUNDARY_FVG_OVERLAP",
        auction_phase=logic.FIRST_RETEST,
        best_progress_r=1.19,
        effort_result=5.0,
        route_rr=20.0,
    )
    assert tuple(weak) == ()


def test_deep_route_has_priority_and_confluence_supplies_earlier_completion():
    both = tuple(
        logic.evidence_tiers(
            family=logic.ACCEPTED,
            location_kind="BOUNDARY_FVG_OVERLAP",
            auction_phase=logic.FIRST_RETEST,
            best_progress_r=1.80,
            effort_result=3.0,
            route_rr=8.0,
        )
    )
    assert [tier.target_r for tier in both] == [1.5, 1.2]
    assert both[0].scenario_rank > both[1].scenario_rank

    confluence_only = tuple(
        logic.evidence_tiers(
            family=logic.ACCEPTED,
            location_kind="TRANSFERRED_BOUNDARY_OB_OVERLAP",
            auction_phase=logic.FIRST_RETEST,
            best_progress_r=1.25,
            effort_result=2.1,
            route_rr=2.0,
        )
    )
    assert len(confluence_only) == 1
    assert confluence_only[0].target_r == 1.2


def test_target_rounding_never_overstates_reward():
    long_target = logic.directional_target(
        entry=100.0, stop=99.0, side="LONG", target_r=1.5, tick=0.3
    )
    short_target = logic.directional_target(
        entry=100.0, stop=101.0, side="SHORT", target_r=1.5, tick=0.3
    )
    assert long_target <= 101.5 + 1e-12
    assert short_target >= 98.5 - 1e-12
    assert logic.route_is_clear(side="LONG", target=long_target, route_price=102.0, tick=0.3)
    assert logic.route_is_clear(side="SHORT", target=short_target, route_price=98.0, tick=0.3)


def test_router_uses_first_qualifying_state_and_single_global_position():
    frame = pd.DataFrame(
        [
            _row("a-low", "s1", "e1", 100, 200, rank=0, target_net=0.4),
            _row("a-high", "s1", "e1", 100, 200, rank=1, tier=1.5, target_net=0.6),
            _row("a-late", "s2", "e1", 110, 210, rank=1, tier=1.5, target_net=0.9),
            _row("b-blocked", "s3", "e2", 150, 250, rank=1, tier=1.5, target_net=0.8),
            _row("c-free", "s4", "e3", 201, 260, rank=0, target_net=0.5),
        ]
    )
    normalized = policy.normalize(frame)
    candidates = policy.episode_candidates(normalized)
    selected, blocked = policy.route_one_account(candidates)
    assert selected.action_id.tolist() == ["a-high", "c-free"]
    assert "b-blocked" in blocked.action_id.tolist()
    assert "a-late" not in candidates.action_id.tolist()


def test_continuous_nav_uses_three_percent_risk():
    frame = policy.normalize(
        pd.DataFrame(
            [
                _row("win", "s1", "e1", 100, 150, net_r=0.5),
                _row("loss", "s2", "e2", 200, 250, net_r=-1.0),
            ]
        )
    )
    trades, nav, summary = policy.account_result(frame, evaluation_calendar_days=2.0)
    expected = (1.0 + 0.03 * 0.5) * (1.0 - 0.03)
    assert len(trades) == 2
    assert math.isclose(summary["ending_nav_multiplier"], expected, rel_tol=0.0, abs_tol=1e-12)
    assert len(nav) == 2
    assert summary["trades_per_calendar_day"] == 1.0
