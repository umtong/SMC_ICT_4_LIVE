"""Fixed-priority multi-timescale liquidity relay for candidate-06 v0.8."""

from __future__ import annotations

from causal_clock import CompletedBarClockMixin
from lrb_types import PrimitiveSnapshot, ScenarioStep
from auction_relay_engine import RollingAuctionLiquidityRelayEngine
from session_relay_engine import SessionLiquidityRelayEngine


class _CausalSessionRelay(CompletedBarClockMixin, SessionLiquidityRelayEngine):
    pass


class _CausalAuctionRelay(CompletedBarClockMixin, RollingAuctionLiquidityRelayEngine):
    pass


class MultiTimescaleLiquidityRelayEngine:
    """Run external-session and completed-hour episodes independently.

    The arbiter never scores or resizes a trade.  When both engines emit on the
    same completed bar, the external session structure has a fixed priority over
    the hourly structure.  The unselected logical episode is explicitly reset.
    """

    def __init__(self, params):
        self.params = dict(params)
        self._session = _CausalSessionRelay(params)
        self._auction = _CausalAuctionRelay(params)
        # Avoid scenario-id collisions when both see the same event time.
        self._auction._sequence = 500_000

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        session_step = self._session.observe(snapshot, allow_new=allow_new)
        auction_step = self._auction.observe(snapshot, allow_new=allow_new)
        transitions = [*session_step.transitions, *auction_step.transitions]

        if session_step.signal is not None:
            reset = self._auction.abort_active(snapshot, "ARBITER_EXTERNAL_SESSION_SIGNAL_SELECTED")
            transitions.extend(reset.transitions)
            return ScenarioStep(transitions=tuple(transitions), signal=session_step.signal)
        if auction_step.signal is not None:
            reset = self._session.abort_active(snapshot, "ARBITER_HOURLY_AUCTION_SIGNAL_SELECTED")
            transitions.extend(reset.transitions)
            return ScenarioStep(transitions=tuple(transitions), signal=auction_step.signal)
        return ScenarioStep(transitions=tuple(transitions))

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        session = self._session.abort_active(snapshot, reason)
        auction = self._auction.abort_active(snapshot, reason)
        return ScenarioStep(transitions=tuple([*session.transitions, *auction.transitions]))
