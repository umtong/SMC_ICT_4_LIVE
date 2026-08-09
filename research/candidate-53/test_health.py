from __future__ import annotations

import math

from candidate53_health_strategy import clipped_probe_r, health_decision

HOUR = 60 * 60 * 1_000_000_000


def test_clip_matches_declared_family_payoff_domain() -> None:
    assert clipped_probe_r(-9.0) == -1.0
    assert clipped_probe_r(9.0) == 3.0
    assert clipped_probe_r(0.75) == 0.75


def test_health_never_reads_future_or_stale_probes() -> None:
    now = 10 * HOUR
    state, score, count = health_decision(
        [
            (now - HOUR, 1.0),
            (now - 2 * HOUR, -0.5),
            (now - 9 * HOUR, 3.0),
            (now + HOUR, 3.0),
        ],
        now_ts=now,
    )
    assert state == "UNKNOWN"
    assert count == 2
    assert math.isclose(score, 0.25)


def test_health_turns_off_only_with_enough_negative_after_cost_evidence() -> None:
    now = 10 * HOUR
    state, score, count = health_decision(
        [(now - k * HOUR, -0.25) for k in (1, 2, 3, 4)],
        now_ts=now,
    )
    assert state == "OFF"
    assert count == 4
    assert score < 0.0


def test_health_turns_back_on_from_shadow_evidence() -> None:
    now = 10 * HOUR
    state, score, count = health_decision(
        [(now - k * HOUR, 0.75) for k in (1, 2, 3, 4)],
        now_ts=now,
    )
    assert state == "ON"
    assert count == 4
    assert score > 0.0
