from __future__ import annotations

import pytest

from smc_ict_4.campaign_policy.attack_ledger import OwnerSide, SourceKey
from smc_ict_4.campaign_policy.integrated_policy import (
    IntegratedCampaignPolicy,
)
from smc_ict_4.campaign_policy.latent_owner import (
    OwnerDirection,
    OwnerIdentity,
    OwnerPhase,
    PosteriorView,
)
from smc_ict_4.campaign_policy.liquidity_graph import SourceIdentity
from smc_ict_4.campaign_policy.route_topology import (
    PriceZone,
    RouteMode,
    RouteOpportunity,
)
from smc_ict_4.episode_policy_live.domain import Bar
from smc_ict_4.episode_policy_live.neutral_policy import (
    ExecutionFeedback,
    MARKET_SYMBOLS,
    MarketFrame,
    TradingPolicy,
)


def opportunity(
    *,
    source_id: str = "BTCUSDT:source",
    side: OwnerSide = OwnerSide.LONG,
    decision: int = 10,
    entry: float = 100.0,
    stop: float = 99.0,
    target: float = 102.0,
) -> RouteOpportunity:
    return RouteOpportunity(
        source_key=SourceKey(source_id, 1),
        attack_ordinal=1,
        target_identity=SourceIdentity(f"{source_id}:target", 1),
        owner_side=side,
        mode=RouteMode.DIRECT_RELEASE,
        decision=decision,
        entry=entry,
        stop=stop,
        target=target,
        zone=PriceZone(99.5, 100.0, decision - 1),
        invalidation=stop,
    )


def posterior(source_id: str, long_probability: float) -> PosteriorView:
    long = OwnerIdentity(source_id, 1, OwnerDirection.LONG)
    short = OwnerIdentity(source_id, 1, OwnerDirection.SHORT)
    phase = {
        long: {
            OwnerPhase.CONTEST: 0.99,
            OwnerPhase.RELEASE: 0.005,
            OwnerPhase.DEFENDED_RETURN: 0.004,
            OwnerPhase.DELIVERY: 0.001,
        },
        short: {
            OwnerPhase.CONTEST: 0.25,
            OwnerPhase.RELEASE: 0.25,
            OwnerPhase.DEFENDED_RETURN: 0.25,
            OwnerPhase.DELIVERY: 0.25,
        },
    }
    identity = {long: long_probability, short: 0.8 - long_probability}
    joint = {
        key: {state: identity[key] * value for state, value in values.items()}
        for key, values in phase.items()
    }
    return PosteriorView(0.2, identity, phase, joint, 0.0)


def frame(close: int, *, interval: int = 5) -> MarketFrame:
    width = interval * 60 * 1_000_000_000
    return MarketFrame(
        tuple(
            Bar(
                symbol=symbol,
                interval_minutes=interval,
                open_time_ns=close - width,
                close_time_ns=close,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=10.0,
                quote_volume=1_000.0,
                taker_buy_quote_volume=550.0,
            )
            for symbol in MARKET_SYMBOLS
        )
    )


def test_policy_implements_execution_neutral_contract() -> None:
    policy = IntegratedCampaignPolicy()

    assert isinstance(policy, TradingPolicy)
    output = policy.on_market_frame(frame(300_000_000_000))
    assert output.intents == ()
    assert output.validity == ()


def test_only_completed_five_minute_global_frames_are_accepted() -> None:
    policy = IntegratedCampaignPolicy()

    with pytest.raises(ValueError, match="completed 5m"):
        policy.on_market_frame(frame(60_000_000_000, interval=1))


def test_expected_r_uses_owner_mixture_once_not_phase_probability_again() -> None:
    candidate = opportunity(target=102.0)
    view = posterior(candidate.source_key.source_id, 0.6)

    evaluation = IntegratedCampaignPolicy._evaluate_opportunity(
        "BTCUSDT", candidate, view,
    )

    # 60% * +2R, 40% * -1R.  The near-zero RELEASE/DELIVERY phase mass is
    # diagnostic and is not multiplied into the already structural route.
    assert evaluation.expected_r == pytest.approx(0.8)
    assert evaluation.owner_probability == pytest.approx(0.6)
    assert evaluation.phase_probability["CONTEST"] == pytest.approx(0.99)


def test_every_structural_candidate_is_preserved_in_replay_diagnostics() -> None:
    policy = IntegratedCampaignPolicy()
    first = opportunity(source_id="BTCUSDT:first")
    second = opportunity(source_id="ETHUSDT:second")
    first_eval = policy._evaluate_opportunity(
        "BTCUSDT", first, posterior(first.source_key.source_id, 0.7),
    )
    second_eval = policy._evaluate_opportunity(
        "ETHUSDT", second, posterior(second.source_key.source_id, 0.3),
    )

    records = policy._diagnostic_records(
        (first_eval, second_eval), first_eval, slot_occupied=False,
    )

    assert len(records) == 2
    assert records[0]["selected"] is True
    assert records[1]["selected"] is False
    assert records[1]["reason"] == "non_positive_expected_r"
    assert records[0]["evidence"]["gross_rr"] == pytest.approx(2.0)


def test_feedback_keeps_one_global_slot_until_terminal_execution_event() -> None:
    policy = IntegratedCampaignPolicy()
    candidate = opportunity()
    evaluation = policy._evaluate_opportunity(
        "BTCUSDT", candidate, posterior(candidate.source_key.source_id, 0.7),
    )
    intent = policy._intent_from_evaluation(evaluation, candidate.decision)
    policy._slot_intent_id = intent.intent_id
    policy._slot_state = "PENDING"

    policy.on_execution_feedback(ExecutionFeedback(intent.intent_id, 11, "SUBMITTED"))
    assert policy.global_slot_state == "PENDING"
    policy.on_execution_feedback(
        ExecutionFeedback(intent.intent_id, 12, "FILLED", 100.0, 1.0),
    )
    assert policy.global_slot_state == "POSITION"
    policy.on_execution_feedback(
        ExecutionFeedback(intent.intent_id, 13, "TARGET_FILLED", 102.0, 1.0),
    )
    assert policy.global_slot_state is None


def test_intent_leaves_sizing_outside_and_has_structural_validity() -> None:
    policy = IntegratedCampaignPolicy()
    candidate = opportunity()
    evaluation = policy._evaluate_opportunity(
        "BTCUSDT", candidate, posterior(candidate.source_key.source_id, 0.7),
    )

    intent = policy._intent_from_evaluation(evaluation, candidate.decision)

    assert intent.valid_until_ns is None
    assert intent.gross_rr == pytest.approx(2.0)
    assert not hasattr(intent, "quantity")
    assert policy.intent_replay_context(intent.intent_id)["owner"] == "LONG"
