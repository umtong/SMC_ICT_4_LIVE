"""Causal lifecycle repair for the crowd-learned horizontal policy.

The first live diagnostic exposed two implementation issues rather than weak
market logic:

* a break-attempt whose next owner candle straddled the exact machine band
  could remain attached to the account for days, even after later candles were
  wholly outside the boundary; and
* once a W/M topology was confirmed, every later trigger pivot logged the same
  confirmation again.

No bar-count expiry is introduced. The first later owner candle whose open and
close are both outside terminates an unresolved break as accepted. A close back
inside remains the only reversal path. Topology confirmation is immutable and
therefore emitted exactly once.
"""
from __future__ import annotations

from domain import Candle, Side
from learned_horizontal_v7 import (
    LearnedHorizontalScenarioEngine,
    LearnedSetupState,
    _TriggerPivot,
)


def _advance_context_setups(
    self: LearnedHorizontalScenarioEngine,
    bar: Candle,
) -> None:
    for setup in list(self._active.values()):
        if setup.state in {
            LearnedSetupState.WAITING_NEXT_CONTEXT,
            LearnedSetupState.WAITING_REENTRY,
        }:
            if setup.side is Side.LONG:
                setup.interaction_extreme = min(setup.interaction_extreme, bar.low)
            else:
                setup.interaction_extreme = max(setup.interaction_extreme, bar.high)

            # The source names the next owner candle as the first acceptance
            # test. If it straddles the exact band, neither acceptance nor
            # reentry has occurred. The first later wholly-outside owner candle
            # closes that causal ambiguity without an arbitrary time timeout.
            if self._outside_open_close(setup.zone, setup.side, bar):
                setup.path = "ACCEPTED_BREAK"
                self._finish(
                    setup,
                    LearnedSetupState.ACCEPTED_BREAK,
                    bar.ts_close_ns,
                    "learned_break_accepted_outside_owner",
                )
                continue

            if self._inside(setup.zone, setup.side, bar.close):
                setup.path = "TRAP_REENTRY"
                setup.reentry_time_ns = bar.ts_close_ns
                setup.state = LearnedSetupState.REENTRY_PENDING_TOPOLOGY
                self._inc("learned_trap_reentry_observed")
                self._trace("learned_trap_reentry_observed", bar.ts_close_ns, setup)
                if setup.topology_confirmed_time_ns is not None:
                    setup.confirmation_time_ns = max(
                        bar.ts_close_ns,
                        setup.topology_confirmed_time_ns,
                    )
                    setup.state = LearnedSetupState.WAITING_RETEST
                    self._inc("learned_trap_confirmed")
                    self._trace("learned_trap_confirmed", bar.ts_close_ns, setup)
            else:
                setup.state = LearnedSetupState.WAITING_REENTRY
            continue

        if setup.state in {
            LearnedSetupState.REENTRY_PENDING_TOPOLOGY,
            LearnedSetupState.WAITING_RETEST,
        }:
            if self._stop_breached(setup, bar):
                self._finish(
                    setup,
                    LearnedSetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "learned_episode_extreme_breached_before_entry",
                )
            elif self._target_spent(setup, bar):
                self._finish(
                    setup,
                    LearnedSetupState.TARGET_SPENT,
                    bar.ts_close_ns,
                    "learned_target_spent_before_entry",
                )


def _update_trap_topology(
    self: LearnedHorizontalScenarioEngine,
    pivot: _TriggerPivot,
) -> None:
    for setup in list(self._active.values()):
        if setup.topology_confirmed_time_ns is not None:
            continue
        if setup.state not in {
            LearnedSetupState.WAITING_NEXT_CONTEXT,
            LearnedSetupState.WAITING_REENTRY,
            LearnedSetupState.REENTRY_PENDING_TOPOLOGY,
        }:
            continue
        if pivot.event_time_ns <= setup.interaction_time_ns:
            continue

        if setup.side is Side.LONG:
            if setup.trap_stage == 0 and pivot.side == "LOW" and pivot.price < setup.zone.lower:
                setup.first_external_pivot_time_ns = pivot.event_time_ns
                setup.trap_stage = 1
            elif (
                setup.trap_stage == 1
                and pivot.side == "HIGH"
                and pivot.price > setup.zone.lower
                and (setup.first_external_pivot_time_ns or 0) < pivot.event_time_ns
            ):
                setup.middle_pivot_time_ns = pivot.event_time_ns
                setup.trap_stage = 2
            elif (
                setup.trap_stage == 2
                and pivot.side == "LOW"
                and pivot.price < setup.zone.lower
                and (setup.middle_pivot_time_ns or 0) < pivot.event_time_ns
            ):
                setup.second_external_pivot_time_ns = pivot.event_time_ns
                setup.topology_confirmed_time_ns = pivot.observed_time_ns
                setup.trap_stage = 3
        else:
            if setup.trap_stage == 0 and pivot.side == "HIGH" and pivot.price > setup.zone.upper:
                setup.first_external_pivot_time_ns = pivot.event_time_ns
                setup.trap_stage = 1
            elif (
                setup.trap_stage == 1
                and pivot.side == "LOW"
                and pivot.price < setup.zone.upper
                and (setup.first_external_pivot_time_ns or 0) < pivot.event_time_ns
            ):
                setup.middle_pivot_time_ns = pivot.event_time_ns
                setup.trap_stage = 2
            elif (
                setup.trap_stage == 2
                and pivot.side == "HIGH"
                and pivot.price > setup.zone.upper
                and (setup.middle_pivot_time_ns or 0) < pivot.event_time_ns
            ):
                setup.second_external_pivot_time_ns = pivot.event_time_ns
                setup.topology_confirmed_time_ns = pivot.observed_time_ns
                setup.trap_stage = 3

        if setup.topology_confirmed_time_ns is None:
            continue
        self._inc("learned_trap_topology_confirmed")
        self._trace(
            "learned_trap_topology_confirmed",
            pivot.observed_time_ns,
            setup,
            first_external_pivot_time_ns=setup.first_external_pivot_time_ns,
            middle_pivot_time_ns=setup.middle_pivot_time_ns,
            second_external_pivot_time_ns=setup.second_external_pivot_time_ns,
        )
        if setup.state is LearnedSetupState.REENTRY_PENDING_TOPOLOGY:
            if setup.reentry_time_ns is None:
                raise RuntimeError("trap reentry state lost reentry time")
            setup.confirmation_time_ns = max(
                setup.reentry_time_ns,
                setup.topology_confirmed_time_ns,
            )
            setup.state = LearnedSetupState.WAITING_RETEST
            self._inc("learned_trap_confirmed")
            self._trace("learned_trap_confirmed", pivot.observed_time_ns, setup)


LearnedHorizontalScenarioEngine._advance_context_setups = _advance_context_setups
LearnedHorizontalScenarioEngine._update_trap_topology = _update_trap_topology
LearnedHorizontalScenarioEngine.TRANSLATION_RULES += (
    "SOURCE_AMBIGUITY_TRANSLATION:FIRST_FULL_OUTSIDE_OWNER_CANDLE_TERMINATES_AMBIGUOUS_BREAK",
    "IMPLEMENTATION_INVARIANT:TRAP_TOPOLOGY_CONFIRMATION_IS_IDEMPOTENT",
)
