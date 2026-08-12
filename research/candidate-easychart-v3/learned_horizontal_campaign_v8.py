"""Causal campaign segmentation for learned Fakeout and Trap boundaries.

A human trader does not treat every nearby horizontal line touched during one
liquidity cascade as a new independent opportunity. At the same time, an old
anchor may legitimately participate again after the earlier reversal has fully
reached its opposing objective, and a stopped reversal must not suppress a new
boundary learned later from entirely fresh defenses.

This module translates that missing discretionary memory into an event-driven
campaign, without a time cooldown, volatility threshold or outcome-fitted
score:

* one unresolved campaign exists per reversal side and auction scale;
* while a break or reversal is active, later same-side boundaries are the same
  causal leg and are suppressed;
* a target-only resolution completes the campaign and removes the lock;
* a stop/failed confirmation/accepted break enters continuation lock;
* a continuation lock resets only after an owner close re-enters the failed
  boundary, or every defense member of a new learned boundary was confirmed
  after the terminal event;
* target and stop reached inside one OHLC bar remain ambiguous and locked;
* a resolution and a new setup on the same bar cannot be ordered causally, so
  the new setup is suppressed until later data.

The campaign is market-state accounting, not trade accounting. It is applied
whether or not the global one-position router ultimately submits the plan, so
trade counts cannot be inflated by skipped duplicate signals.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from domain import Candle, Side
from learned_horizontal_confirmation_v7 import (
    ConfirmationCloseLearnedHorizontalScenarioEngine,
)
from learned_horizontal_v7 import (
    LearnedHorizontalSetup,
    LearnedHorizontalZone,
    LearnedSetupState,
)


class CampaignPhase(str, Enum):
    BREAK_PENDING = "BREAK_PENDING"
    REVERSAL_ACTIVE = "REVERSAL_ACTIVE"
    CONTINUATION_LOCK = "CONTINUATION_LOCK"
    AMBIGUOUS_LOCK = "AMBIGUOUS_LOCK"


@dataclass(slots=True)
class LearnedBoundaryCampaign:
    reversal_side: Side
    setup_id: str
    zone: LearnedHorizontalZone
    member_ids: tuple[str, ...]
    phase: CampaignPhase
    created_time_ns: int
    activation_time_ns: int | None
    terminal_time_ns: int | None
    stop_price: float | None
    target_price: float | None
    terminal_reason: str | None = None


class CampaignLearnedHorizontalScenarioEngine(
    ConfirmationCloseLearnedHorizontalScenarioEngine,
):
    """Confirmation-close learned boundary policy with causal episode memory."""

    ENTRY_POLICY = "CONFIRMATION_CLOSE_CAMPAIGN"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.campaigns: dict[Side, LearnedBoundaryCampaign] = {}
        self._campaign_setup_by_id: dict[str, LearnedHorizontalSetup] = {}
        self._closed_campaign_setup_ids: set[str] = set()
        self._same_bar_blocks: dict[Side, int] = {}

    def _member_observation_times(
        self,
        zone: LearnedHorizontalZone,
    ) -> tuple[int, ...] | None:
        by_id = {
            interval.touch_id: interval.observed_time_ns
            for interval in self.detector.intervals
        }
        if any(member_id not in by_id for member_id in zone.member_ids):
            return None
        return tuple(by_id[member_id] for member_id in zone.member_ids)

    def _fresh_after_terminal(
        self,
        zone: LearnedHorizontalZone,
        terminal_time_ns: int | None,
    ) -> bool:
        if terminal_time_ns is None:
            return False
        observed = self._member_observation_times(zone)
        if observed is None or not observed:
            self._inc("campaign_freshness_member_lookup_failed")
            return False
        return all(time_ns > terminal_time_ns for time_ns in observed)

    @staticmethod
    def _reentered_failed_boundary(
        campaign: LearnedBoundaryCampaign,
        bar: Candle,
    ) -> bool:
        if campaign.reversal_side is Side.LONG:
            return bar.close > campaign.zone.upper
        return bar.close < campaign.zone.lower

    def _record_campaign(
        self,
        kind: str,
        time_ns: int,
        campaign: LearnedBoundaryCampaign,
        **values: Any,
    ) -> None:
        self._inc(kind)
        self._trace(
            kind,
            time_ns,
            None,
            campaign_reversal_side=campaign.reversal_side.name,
            campaign_phase=campaign.phase.value,
            campaign_setup_id=campaign.setup_id,
            campaign_zone_id=campaign.zone.zone_id,
            campaign_created_time_ns=campaign.created_time_ns,
            campaign_activation_time_ns=campaign.activation_time_ns,
            campaign_terminal_time_ns=campaign.terminal_time_ns,
            campaign_stop_price=campaign.stop_price,
            campaign_target_price=campaign.target_price,
            campaign_terminal_reason=campaign.terminal_reason,
            **values,
        )

    def _close_campaign(
        self,
        campaign: LearnedBoundaryCampaign,
        time_ns: int,
        reason: str,
        *,
        block_same_bar: bool,
    ) -> None:
        current = self.campaigns.get(campaign.reversal_side)
        if current is campaign:
            self.campaigns.pop(campaign.reversal_side, None)
        self._closed_campaign_setup_ids.add(campaign.setup_id)
        if block_same_bar:
            self._same_bar_blocks[campaign.reversal_side] = time_ns
        campaign.terminal_time_ns = time_ns
        campaign.terminal_reason = reason
        self._record_campaign(reason, time_ns, campaign)

    def _lock_campaign(
        self,
        campaign: LearnedBoundaryCampaign,
        time_ns: int,
        reason: str,
        *,
        ambiguous: bool = False,
    ) -> None:
        requested = (
            CampaignPhase.AMBIGUOUS_LOCK
            if ambiguous
            else CampaignPhase.CONTINUATION_LOCK
        )
        if (
            campaign.phase is requested
            and campaign.terminal_time_ns == time_ns
            and campaign.terminal_reason == reason
        ):
            return
        campaign.phase = requested
        campaign.terminal_time_ns = time_ns
        campaign.terminal_reason = reason
        self._same_bar_blocks[campaign.reversal_side] = time_ns
        self._record_campaign(reason, time_ns, campaign)

    def _campaign_for_setup(
        self,
        setup: LearnedHorizontalSetup,
        time_ns: int,
    ) -> LearnedBoundaryCampaign:
        existing = self.campaigns.get(setup.side)
        if existing is not None and existing.setup_id == setup.setup_id:
            return existing
        campaign = LearnedBoundaryCampaign(
            reversal_side=setup.side,
            setup_id=setup.setup_id,
            zone=setup.zone,
            member_ids=setup.zone.member_ids,
            phase=CampaignPhase.BREAK_PENDING,
            created_time_ns=setup.interaction_time_ns,
            activation_time_ns=None,
            terminal_time_ns=None,
            stop_price=self._stop_price(setup),
            target_price=setup.target_price,
        )
        self.campaigns[setup.side] = campaign
        self._campaign_setup_by_id[setup.setup_id] = setup
        self._record_campaign("campaign_created", time_ns, campaign)
        return campaign

    def _sync_setup_campaign(
        self,
        setup: LearnedHorizontalSetup,
        time_ns: int,
    ) -> None:
        if setup.setup_id in self._closed_campaign_setup_ids:
            return
        campaign = self.campaigns.get(setup.side)
        if campaign is None or campaign.setup_id != setup.setup_id:
            return

        state = setup.state
        if state in {
            LearnedSetupState.WAITING_NEXT_CONTEXT,
            LearnedSetupState.WAITING_REENTRY,
            LearnedSetupState.REENTRY_PENDING_TOPOLOGY,
        }:
            campaign.phase = CampaignPhase.BREAK_PENDING
            return

        if state in {LearnedSetupState.WAITING_RETEST, LearnedSetupState.PLANNED}:
            # A terminal campaign lock is market memory and must never be
            # reactivated merely because the setup object remains PLANNED.
            if campaign.phase is CampaignPhase.BREAK_PENDING:
                campaign.phase = CampaignPhase.REVERSAL_ACTIVE
                campaign.activation_time_ns = (
                    setup.confirmation_time_ns
                    if setup.confirmation_time_ns is not None
                    else time_ns
                )
                campaign.stop_price = self._stop_price(setup)
                campaign.target_price = setup.target_price
                self._record_campaign(
                    "campaign_reversal_activated",
                    time_ns,
                    campaign,
                    setup_state=state.value,
                    scenario_path=setup.path,
                )
            return

        if state is LearnedSetupState.TARGET_SPENT:
            self._close_campaign(
                campaign,
                time_ns,
                "campaign_resolved_target_before_entry",
                block_same_bar=True,
            )
            return

        if state in {
            LearnedSetupState.ACCEPTED_BREAK,
            LearnedSetupState.INVALIDATED,
            LearnedSetupState.FIRST_RETEST_UNRESOLVED,
            LearnedSetupState.NO_TRADE_GEOMETRY,
            LearnedSetupState.NO_TARGET,
        }:
            if campaign.phase not in {
                CampaignPhase.CONTINUATION_LOCK,
                CampaignPhase.AMBIGUOUS_LOCK,
            }:
                self._lock_campaign(
                    campaign,
                    time_ns,
                    f"campaign_locked_after_{state.value.lower()}",
                )

    def _sync_campaigns(self, time_ns: int) -> None:
        for campaign in list(self.campaigns.values()):
            setup = self._campaign_setup_by_id.get(campaign.setup_id)
            if setup is not None:
                self._sync_setup_campaign(setup, time_ns)

    def _observe_reversal_campaigns(
        self,
        bar: Candle,
        *,
        owner_bar: bool,
    ) -> None:
        for campaign in list(self.campaigns.values()):
            if campaign.phase in {
                CampaignPhase.CONTINUATION_LOCK,
                CampaignPhase.AMBIGUOUS_LOCK,
            }:
                if owner_bar and self._reentered_failed_boundary(campaign, bar):
                    self._close_campaign(
                        campaign,
                        bar.ts_close_ns,
                        "campaign_reset_owner_close_reentry",
                        block_same_bar=True,
                    )
                continue

            if campaign.phase is not CampaignPhase.REVERSAL_ACTIVE:
                continue
            activation = campaign.activation_time_ns
            if activation is None or bar.ts_close_ns <= activation:
                continue
            stop = campaign.stop_price
            target = campaign.target_price
            if stop is None or target is None:
                self._lock_campaign(
                    campaign,
                    bar.ts_close_ns,
                    "campaign_missing_resolution_geometry",
                )
                continue
            stop_hit = (
                bar.low <= stop
                if campaign.reversal_side is Side.LONG
                else bar.high >= stop
            )
            target_hit = (
                bar.high >= target
                if campaign.reversal_side is Side.LONG
                else bar.low <= target
            )
            if stop_hit and target_hit:
                self._lock_campaign(
                    campaign,
                    bar.ts_close_ns,
                    "campaign_ambiguous_target_and_stop_same_bar",
                    ambiguous=True,
                )
            elif target_hit:
                self._close_campaign(
                    campaign,
                    bar.ts_close_ns,
                    "campaign_resolved_target",
                    block_same_bar=True,
                )
            elif stop_hit:
                self._lock_campaign(
                    campaign,
                    bar.ts_close_ns,
                    "campaign_stopped_continuation_lock",
                )

    def _suppressed_setup(
        self,
        zone: LearnedHorizontalZone,
        bar: Candle,
        index: int,
        reason: str,
        campaign: LearnedBoundaryCampaign | None,
        *,
        fakeout: bool,
    ) -> LearnedHorizontalSetup:
        side = self._trade_side(zone)
        self.detector.consume(zone, bar.ts_close_ns)
        setup = LearnedHorizontalSetup(
            setup_id=(
                f"{self.scale_name}:CAMPAIGN_SUPPRESSED:{zone.zone_id}:"
                f"{bar.ts_close_ns}"
            ),
            zone=zone,
            side=side,
            path="CAMPAIGN_DUPLICATE",
            state=LearnedSetupState.DUPLICATE_EPISODE,
            interaction_time_ns=bar.ts_close_ns,
            interaction_index=index,
            interaction_extreme=bar.low if side is Side.LONG else bar.high,
            target_zone=None,
            target_price=None,
            terminal_reason=reason,
        )
        self.setups.append(setup)
        self._audit(zone)
        self._inc(reason)
        self._trace(
            reason,
            bar.ts_close_ns,
            setup,
            attempted_fakeout=fakeout,
            active_campaign_setup_id=(
                None if campaign is None else campaign.setup_id
            ),
            active_campaign_phase=(
                None if campaign is None else campaign.phase.value
            ),
            active_campaign_zone_id=(
                None if campaign is None else campaign.zone.zone_id
            ),
            active_campaign_terminal_time_ns=(
                None if campaign is None else campaign.terminal_time_ns
            ),
        )
        return setup

    def _new_setup(
        self,
        zone: LearnedHorizontalZone,
        bar: Candle,
        index: int,
        *,
        fakeout: bool,
    ) -> LearnedHorizontalSetup:
        side = self._trade_side(zone)
        campaign = self.campaigns.get(side)

        if self._same_bar_blocks.get(side) == bar.ts_close_ns:
            return self._suppressed_setup(
                zone,
                bar,
                index,
                "campaign_same_bar_reordering_suppressed",
                campaign,
                fakeout=fakeout,
            )

        if campaign is not None and campaign.phase in {
            CampaignPhase.BREAK_PENDING,
            CampaignPhase.REVERSAL_ACTIVE,
        }:
            return self._suppressed_setup(
                zone,
                bar,
                index,
                "campaign_active_same_side_suppressed",
                campaign,
                fakeout=fakeout,
            )

        if campaign is not None and campaign.phase in {
            CampaignPhase.CONTINUATION_LOCK,
            CampaignPhase.AMBIGUOUS_LOCK,
        }:
            if not self._fresh_after_terminal(zone, campaign.terminal_time_ns):
                return self._suppressed_setup(
                    zone,
                    bar,
                    index,
                    "campaign_stale_boundary_suppressed",
                    campaign,
                    fakeout=fakeout,
                )
            self._close_campaign(
                campaign,
                bar.ts_close_ns,
                "campaign_reset_fully_fresh_boundary",
                block_same_bar=False,
            )
            self._same_bar_blocks.pop(side, None)

        setup = super()._new_setup(zone, bar, index, fakeout=fakeout)
        if setup.state is not LearnedSetupState.DUPLICATE_EPISODE:
            self._campaign_for_setup(setup, bar.ts_close_ns)
            self._sync_setup_campaign(setup, bar.ts_close_ns)
        return setup

    def _context_bar(self, bar: Candle) -> list[Any]:
        self._observe_reversal_campaigns(bar, owner_bar=True)
        plans = super()._context_bar(bar)
        self._sync_campaigns(bar.ts_close_ns)
        return plans

    def _trigger_bar(self, bar: Candle) -> list[Any]:
        self._observe_reversal_campaigns(bar, owner_bar=False)
        plans = super()._trigger_bar(bar)
        self._sync_campaigns(bar.ts_close_ns)
        return plans


CampaignLearnedHorizontalScenarioEngine.TRANSLATION_RULES += (
    "HUMAN_NATURAL_INFERENCE:ONE_UNRESOLVED_DIRECTIONAL_LEG_IS_ONE_CAMPAIGN",
    "SOURCE_AMBIGUITY_TRANSLATION:TARGET_COMPLETION_ENDS_THE_CAMPAIGN",
    "SOURCE_AMBIGUITY_TRANSLATION:STOP_OR_ACCEPTED_BREAK_CREATES_CONTINUATION_LOCK",
    "SOURCE_AMBIGUITY_TRANSLATION:ONLY_POST_TERMINAL_DEFENSES_DEFINE_A_FRESH_BOUNDARY",
    "EXTERNAL_METHOD:EVENT_AND_PRICE_HYSTERESIS_PREVENTS_CAUSAL_STATE_CHATTER",
    "IMPLEMENTATION_INVARIANT:SAME_BAR_TARGET_STOP_OR_REORDERING_REMAINS_LOCKED",
)
