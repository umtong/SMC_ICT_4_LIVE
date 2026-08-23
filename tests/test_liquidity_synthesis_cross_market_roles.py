from __future__ import annotations

import pytest

from smc_ict_4.episode_policy_live.cross_market_roles import (
    AcceptedRepricingPhase,
    CausalScalar,
    EventLeadershipRole,
    EventPrice,
    PeerParticipation,
    TrailingAuctionRole,
    analyze_cross_market_roles,
)
from smc_ict_4.episode_policy_live.directional_context import DirectionalContext


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SWEEP = 100
DECISION = 300


def context(symbol: str, score: float | None, *, side: str = "LONG", ts: int = SWEEP):
    return DirectionalContext(
        symbol=symbol,
        side=side,
        decision_time_ns=ts,
        atr_price=1.0,
        horizons=(),
        trend_alignment=score,
        trend_consensus=None,
        path_efficiency=None,
        signed_flow_share=None,
        activity_ratio=None,
        common_component=None,
        symbol_residual=None,
    )


def path(start: float, middle: float, end: float):
    return (
        EventPrice(SWEEP, start),
        EventPrice(200, middle),
        EventPrice(DECISION, end),
    )


def contexts(scores: dict[str, float] | None = None):
    scores = scores or {
        "BTCUSDT": 0.8,
        "ETHUSDT": 0.6,
        "SOLUSDT": 0.4,
        "XRPUSDT": 0.2,
    }
    return {symbol: context(symbol, scores[symbol]) for symbol in SYMBOLS}


def notionals():
    return {
        "BTCUSDT": CausalScalar(SWEEP, 1000.0),
        "ETHUSDT": CausalScalar(SWEEP, 800.0),
        "SOLUSDT": CausalScalar(SWEEP, 600.0),
        "XRPUSDT": CausalScalar(SWEEP, 400.0),
    }


def progress(value: float = 0.6):
    return {symbol: CausalScalar(SWEEP, value) for symbol in SYMBOLS}


def test_unanimous_event_lead_and_early_repricing_are_descriptive():
    result = analyze_cross_market_roles(
        symbols=SYMBOLS,
        symbol="ETHUSDT",
        side="LONG",
        sweep_time_ns=SWEEP,
        decision_time_ns=DECISION,
        event_paths={
            "BTCUSDT": path(100.0, 100.5, 101.0),
            "ETHUSDT": path(100.0, 101.0, 103.0),
            "SOLUSDT": path(100.0, 100.4, 100.8),
            "XRPUSDT": path(100.0, 100.3, 100.7),
        },
        directional_contexts=contexts(),
        trailing_quote_notionals=notionals(),
        auction_progress_units=progress(),
    )

    assert result.synchronized_event_complete
    assert result.peer_participation is PeerParticipation.UNANIMOUS
    assert result.event_direction_rank == 1
    assert result.event_leadership_role is EventLeadershipRole.UNANIMOUS_LEAD
    assert result.trailing_direction_rank == 2
    assert result.trailing_auction_role is TrailingAuctionRole.FRONT_COHORT
    assert result.local_event_path_efficiency == pytest.approx(1.0)
    assert result.accepted_repricing_phase is AcceptedRepricingPhase.EARLY_ACCEPTED_REPRICING
    assert "approved" not in result.to_dict()


def test_completed_auction_units_distinguish_extension_without_v4_floor_bundle():
    paths = {
        "BTCUSDT": path(100.0, 100.5, 101.0),
        "ETHUSDT": path(100.0, 101.0, 103.0),
        "SOLUSDT": path(100.0, 100.4, 100.8),
        "XRPUSDT": path(100.0, 100.3, 100.7),
    }
    units = progress(0.7)
    units["ETHUSDT"] = CausalScalar(SWEEP, 1.05)
    result = analyze_cross_market_roles(
        symbols=SYMBOLS,
        symbol="ETHUSDT",
        side="LONG",
        sweep_time_ns=SWEEP,
        decision_time_ns=DECISION,
        event_paths=paths,
        directional_contexts=contexts(),
        trailing_quote_notionals=notionals(),
        auction_progress_units=units,
    )

    assert result.candidate_auction_progress_units == 1.05
    assert result.accepted_repricing_phase is AcceptedRepricingPhase.ALREADY_EXTENDED


def test_dominant_quorum_and_independent_lead_remain_distinct_roles():
    quorum = analyze_cross_market_roles(
        symbols=SYMBOLS,
        symbol="SOLUSDT",
        side="LONG",
        sweep_time_ns=SWEEP,
        decision_time_ns=DECISION,
        event_paths={
            "BTCUSDT": path(100.0, 100.5, 102.0),
            "ETHUSDT": path(100.0, 100.4, 101.0),
            "SOLUSDT": path(100.0, 102.0, 104.0),
            "XRPUSDT": path(100.0, 99.8, 99.5),
        },
    )
    assert quorum.peer_participation is PeerParticipation.DOMINANT_QUORUM
    assert quorum.event_leadership_role is EventLeadershipRole.QUORUM_LEAD
    assert quorum.independently_leads_event is False

    independent = analyze_cross_market_roles(
        symbols=SYMBOLS,
        symbol="SOLUSDT",
        side="LONG",
        sweep_time_ns=SWEEP,
        decision_time_ns=DECISION,
        event_paths={
            "BTCUSDT": path(100.0, 100.2, 101.0),
            "ETHUSDT": path(100.0, 99.8, 99.5),
            "SOLUSDT": path(100.0, 102.0, 104.0),
            "XRPUSDT": path(100.0, 99.7, 99.3),
        },
    )
    assert independent.peer_participation is PeerParticipation.DIVIDED
    assert independent.event_leadership_role is EventLeadershipRole.INDEPENDENT_LEAD
    assert independent.independently_leads_event is True


def test_event_laggard_transfer_is_not_the_trailing_auction_laggard():
    result = analyze_cross_market_roles(
        symbols=SYMBOLS,
        symbol="SOLUSDT",
        side="LONG",
        sweep_time_ns=SWEEP,
        decision_time_ns=DECISION,
        event_paths={
            "BTCUSDT": path(100.0, 101.0, 104.0),
            "ETHUSDT": path(100.0, 101.0, 103.0),
            "SOLUSDT": path(100.0, 100.1, 100.4),
            "XRPUSDT": path(100.0, 100.8, 102.0),
        },
        directional_contexts=contexts(
            {
                "BTCUSDT": 0.8,
                "ETHUSDT": 0.7,
                "SOLUSDT": 0.4,
                "XRPUSDT": 0.1,
            }
        ),
        trailing_quote_notionals=notionals(),
    )

    assert result.event_direction_rank == 4
    assert result.event_leadership_role is EventLeadershipRole.LAGGARD
    assert result.trailing_direction_rank == 3
    assert result.trailing_auction_role is TrailingAuctionRole.INTERMEDIATE
    assert result.accepted_repricing_phase is AcceptedRepricingPhase.LAGGARD_TRANSFER


def test_missing_market_state_stays_unknown_instead_of_becoming_zero():
    result = analyze_cross_market_roles(
        symbols=SYMBOLS,
        symbol="SOLUSDT",
        side="LONG",
        sweep_time_ns=SWEEP,
        decision_time_ns=DECISION,
        event_paths={
            "BTCUSDT": path(100.0, 101.0, 102.0),
            "ETHUSDT": path(100.0, 101.0, 102.0),
            "SOLUSDT": path(100.0, 101.0, 103.0),
        },
        directional_contexts=None,
    )

    assert not result.synchronized_event_complete
    assert result.event_direction_rank is None
    assert result.event_leadership_role is EventLeadershipRole.UNKNOWN
    assert result.peer_participation is PeerParticipation.UNKNOWN
    assert result.trailing_direction_rank is None
    assert result.accepted_repricing_phase is AcceptedRepricingPhase.UNKNOWN


def test_directional_context_after_sweep_is_rejected_as_lookahead():
    future = {symbol: context(symbol, 0.5, ts=SWEEP + 1) for symbol in SYMBOLS}
    with pytest.raises(ValueError, match="after the sweep"):
        analyze_cross_market_roles(
            symbols=SYMBOLS,
            symbol="SOLUSDT",
            side="LONG",
            sweep_time_ns=SWEEP,
            decision_time_ns=DECISION,
            event_paths={symbol: path(100.0, 101.0, 102.0) for symbol in SYMBOLS},
            directional_contexts=future,
        )


def test_future_progress_scalar_is_rejected_as_lookahead():
    units = progress()
    units["SOLUSDT"] = CausalScalar(SWEEP + 1, 0.4)
    with pytest.raises(ValueError, match="causal boundary"):
        analyze_cross_market_roles(
            symbols=SYMBOLS,
            symbol="SOLUSDT",
            side="LONG",
            sweep_time_ns=SWEEP,
            decision_time_ns=DECISION,
            event_paths={symbol: path(100.0, 101.0, 102.0) for symbol in SYMBOLS},
            auction_progress_units=units,
        )


def test_short_side_signs_event_and_path_efficiency_in_trade_direction():
    result = analyze_cross_market_roles(
        symbols=SYMBOLS,
        symbol="BTCUSDT",
        side="SHORT",
        sweep_time_ns=SWEEP,
        decision_time_ns=DECISION,
        event_paths={
            "BTCUSDT": path(100.0, 99.0, 97.0),
            "ETHUSDT": path(100.0, 99.5, 98.0),
            "SOLUSDT": path(100.0, 99.5, 98.5),
            "XRPUSDT": path(100.0, 100.2, 99.0),
        },
    )

    assert result.signed_event_returns["BTCUSDT"] > 0.0
    assert result.event_direction_rank == 1
    assert result.local_event_path_efficiency == pytest.approx(1.0)
