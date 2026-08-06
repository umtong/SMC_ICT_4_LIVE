"""Fixed-auction engine which preserves context for failed-acceptance traps."""

from __future__ import annotations

from fixed_interval_auction_engine import FixedIntervalAuctionLiquidityRelayEngine
from lrb_types import ScenarioSignal, ScenarioStep


class FailedAuctionTrapRelayEngine(FixedIntervalAuctionLiquidityRelayEngine):
    """Attach the completed auction geometry to every armed continuation signal."""

    def _emit(self, snapshot, episode, stop, target, target_reason, reason):
        step = super()._emit(snapshot, episode, stop, target, target_reason, reason)
        signal = step.signal
        if signal is None:
            return step
        details = {
            **dict(signal.details),
            "episode_extreme": float(episode.extreme),
            "auction_range_high": None if episode.range_high is None else float(episode.range_high),
            "auction_range_low": None if episode.range_low is None else float(episode.range_low),
            "auction_range_mid": (
                None
                if episode.range_high is None or episode.range_low is None
                else float((episode.range_high + episode.range_low) / 2.0)
            ),
            "original_acceptance_direction": episode.direction,
            "original_acceptance_side": episode.side,
            "level_name": episode.level_name,
            "window": episode.window,
        }
        enriched = ScenarioSignal(
            scenario_id=signal.scenario_id,
            family=signal.family,
            direction=signal.direction,
            observed_ts_ns=signal.observed_ts_ns,
            reference_entry=signal.reference_entry,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            target_reason=signal.target_reason,
            atr=signal.atr,
            liquidity_level=signal.liquidity_level,
            details=details,
        )
        return ScenarioStep(transitions=step.transitions, signal=enriched)
