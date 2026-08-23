from __future__ import annotations

import json

import pytest

from smc_ict_4.campaign_policy.attack_ledger import (
    AttackLedger,
    AttackLedgerError,
    AttackOutcome,
    CampaignPhase,
    EventKind,
    OwnerSide,
    SourceKey,
    SourceSide,
    SourceSpec,
    TerminalReason,
)


def spec(
    source_id: str,
    generation: int = 1,
    *,
    side: SourceSide = SourceSide.HIGH,
    parent: str = "parent",
    parent_generation: int = 1,
    observed: int = 1,
) -> SourceSpec:
    return SourceSpec(
        key=SourceKey(source_id, generation),
        side=side,
        tick_size=0.5,
        observed_time_ns=observed,
        parent_id=parent,
        parent_generation=parent_generation,
    )


def test_first_touch_starts_one_campaign_and_unanswered_extensions_update_attack() -> None:
    ledger = AttackLedger("BTCUSDT")
    source = spec("weekly-high")
    ledger.register_source(source)

    ledger.record_touch(source.key, time_ns=10, extreme=100.0, physical_attack_id="a1")
    ledger.record_touch(source.key, time_ns=11, extreme=101.0, physical_attack_id="a1-extension")

    campaign = ledger.campaign(source.key)
    assert campaign is not None
    assert campaign.campaign_id == "BTCUSDT:weekly-high:1"
    assert len(campaign.attacks) == 1
    assert campaign.attacks[0].extreme == 101.0
    assert campaign.attacks[0].outcome is AttackOutcome.ACTIVE
    assert [event.kind for event in ledger.events] == [
        EventKind.CAMPAIGN_STARTED,
        EventKind.ATTACK_EXTENDED,
    ]


def test_source_and_parent_generations_are_strictly_positive() -> None:
    with pytest.raises(AttackLedgerError, match="source generation"):
        SourceKey("bad", 0)
    with pytest.raises(AttackLedgerError, match="parent generation"):
        SourceSpec(SourceKey("ok", 1), SourceSide.HIGH, 0.5, 1, "parent", 0)


def test_completed_response_freezes_control_and_tick_fresh_extreme_appends_reattack() -> None:
    ledger = AttackLedger("BTCUSDT")
    source = spec("weekly-high")
    ledger.register_source(source)
    ledger.record_touch(source.key, time_ns=10, extreme=100.0)
    ledger.observe_response(source.key, time_ns=11, response_extreme=98.0)
    ledger.observe_response(
        source.key,
        time_ns=12,
        response_extreme=97.0,
        completed=True,
        frozen_control=99.0,
    )

    # A retouch inside one tick remains the completed physical attack.
    ledger.record_touch(source.key, time_ns=13, extreme=100.49)
    assert len(ledger.campaign(source.key).attacks) == 1  # type: ignore[union-attr]

    ledger.record_touch(source.key, time_ns=14, extreme=100.5)
    campaign = ledger.campaign(source.key)
    assert campaign is not None
    assert len(campaign.attacks) == 2
    first, second = campaign.attacks
    assert first.end_time_ns == 12
    assert first.intervening_response_extreme == 97.0
    assert first.frozen_control == 99.0
    assert first.outcome is AttackOutcome.RESPONSE_COMPLETED
    assert second.ordinal == 2
    assert second.start_time_ns == 14
    assert second.outcome is AttackOutcome.ACTIVE


def test_claim_changes_owner_phase_only_and_repeated_attack_does_not_consume_source() -> None:
    ledger = AttackLedger("BTCUSDT")
    source = spec("external-low", side=SourceSide.LOW)
    ledger.register_source(source)
    ledger.record_touch(source.key, time_ns=10, extreme=90.0)
    before = ledger.campaign(source.key).attacks  # type: ignore[union-attr]
    ledger.claim(source.key, time_ns=11, owner=OwnerSide.LONG)
    claimed = ledger.campaign(source.key)
    assert claimed is not None
    assert claimed.phase is CampaignPhase.CLAIMED
    assert claimed.owner is OwnerSide.LONG
    assert claimed.attacks == before

    ledger.observe_response(
        source.key,
        time_ns=12,
        response_extreme=92.0,
        completed=True,
        frozen_control=91.0,
    )
    ledger.record_touch(source.key, time_ns=13, extreme=89.5)
    after = ledger.campaign(source.key)
    assert after is not None
    assert after.campaign_id == claimed.campaign_id
    assert after.phase is CampaignPhase.CLAIMED
    assert len(after.attacks) == 2


def test_other_sources_are_independent_while_campaign_is_active() -> None:
    ledger = AttackLedger("BTCUSDT")
    high = spec("high")
    low = spec("low", side=SourceSide.LOW)
    ledger.register_source(high)
    ledger.register_source(low)
    ledger.record_touch(high.key, time_ns=10, extreme=110.0)
    ledger.record_touch(low.key, time_ns=10, extreme=90.0)
    ledger.claim(high.key, time_ns=11, owner=OwnerSide.SHORT)

    assert len(ledger.campaigns) == 2
    assert ledger.campaign(high.key).owner is OwnerSide.SHORT  # type: ignore[union-attr]
    assert ledger.campaign(low.key).owner is None  # type: ignore[union-attr]


def test_multiclock_source_and_physical_aliases_dedupe_same_attack() -> None:
    ledger = AttackLedger("BTCUSDT")
    canonical = spec("parent-high")
    alias = SourceKey("parent-high-15m-view", 1)
    ledger.register_source(canonical)
    ledger.register_source_alias(alias, canonical.key)
    ledger.register_physical_alias("15m:attack-7", "1m:attack-7")

    ledger.record_touch(
        canonical.key, time_ns=10, extreme=100.0, physical_attack_id="1m:attack-7"
    )
    ledger.record_touch(alias, time_ns=10, extreme=100.2, physical_attack_id="15m:attack-7")

    campaign = ledger.campaign(alias)
    assert campaign is not None
    assert len(ledger.campaigns) == 1
    assert len(campaign.attacks) == 1
    assert campaign.attacks[0].extreme == 100.2
    assert ledger.events[-1].kind is EventKind.PHYSICAL_ATTACK_DEDUPED


@pytest.mark.parametrize(
    ("terminal", "reason"),
    [
        ("objective", TerminalReason.OBJECTIVE_TOUCHED),
        ("source", TerminalReason.SOURCE_INVALIDATED),
    ],
)
def test_only_structural_lifecycle_events_retire_and_never_reopen(terminal: str, reason: TerminalReason) -> None:
    ledger = AttackLedger("BTCUSDT")
    source = spec("high")
    ledger.register_source(source)
    ledger.record_touch(source.key, time_ns=10, extreme=100.0)
    if terminal == "objective":
        ledger.objective_touched(source.key, time_ns=20)
    else:
        ledger.source_invalidated(source.key, time_ns=20)

    campaign = ledger.campaign(source.key)
    assert campaign is not None
    assert campaign.phase is CampaignPhase.TERMINAL
    assert campaign.terminal_reason is reason
    assert ledger.record_touch(source.key, time_ns=10_000_000, extreme=200.0) == ()
    assert ledger.campaign(source.key) == campaign


def test_new_parent_generation_supersedes_every_old_sibling_campaign() -> None:
    ledger = AttackLedger("BTCUSDT")
    old_high = spec("old-high", parent="balance", parent_generation=3)
    old_low = spec("old-low", side=SourceSide.LOW, parent="balance", parent_generation=3)
    ledger.register_source(old_high)
    ledger.register_source(old_low)
    ledger.record_touch(old_high.key, time_ns=10, extreme=100.0)
    ledger.record_touch(old_low.key, time_ns=10, extreme=90.0)

    ledger.register_source(
        spec("new-high", parent="balance", parent_generation=4, observed=20)
    )

    for key in (old_high.key, old_low.key):
        campaign = ledger.campaign(key)
        assert campaign is not None
        assert campaign.phase is CampaignPhase.TERMINAL
        assert campaign.terminal_reason is TerminalReason.PARENT_GENERATION_SUPERSEDED
    with pytest.raises(AttackLedgerError, match="superseded parent generation"):
        ledger.register_source(
            spec("stale", parent="balance", parent_generation=3, observed=21)
        )


def test_old_owner_terminal_and_opposite_claim_are_forbidden_in_either_call_order() -> None:
    for claim_first in (False, True):
        ledger = AttackLedger("BTCUSDT")
        old = spec("old")
        new = spec("new")
        ledger.register_source(old)
        ledger.register_source(new)
        ledger.record_touch(old.key, time_ns=10, extreme=100.0)
        ledger.record_touch(new.key, time_ns=10, extreme=101.0)
        ledger.claim(old.key, time_ns=11, owner=OwnerSide.LONG)
        if claim_first:
            ledger.claim(new.key, time_ns=20, owner=OwnerSide.SHORT)
            with pytest.raises(AttackLedgerError, match="opposite same-bar claim"):
                ledger.source_invalidated(old.key, time_ns=20)
        else:
            ledger.source_invalidated(old.key, time_ns=20)
            with pytest.raises(AttackLedgerError, match="opposite ownership"):
                ledger.claim(new.key, time_ns=20, owner=OwnerSide.SHORT)


def test_export_restore_is_deterministic_and_continues_sequence() -> None:
    ledger = AttackLedger("BTCUSDT")
    source = spec("high")
    ledger.register_source(source)
    ledger.record_touch(source.key, time_ns=10, extreme=100.0, physical_attack_id="p1")
    ledger.observe_response(
        source.key,
        time_ns=12,
        response_extreme=98.0,
        completed=True,
        frozen_control=99.0,
    )
    ledger.claim(source.key, time_ns=13, owner=OwnerSide.SHORT)

    checkpoint = ledger.export_state()
    restored = AttackLedger.restore_state(json.loads(json.dumps(checkpoint, sort_keys=True)))
    assert restored.export_state() == checkpoint

    ledger.record_touch(source.key, time_ns=14, extreme=100.5, physical_attack_id="p2")
    restored.record_touch(source.key, time_ns=14, extreme=100.5, physical_attack_id="p2")
    assert restored.export_state() == ledger.export_state()
