"""Causal target ladder for confirmed aggressor-flow reversals.

The stop logic and all signal thresholds remain unchanged. The only behavioral
change is target selection: test opposing internal liquidity first, then the
opposing external pool, and accept the first causal level which provides the
predeclared minimum reward-to-risk. This mirrors a trader's ordered liquidity
map instead of rejecting an episode merely because the nearest pool is too
close.
"""
from __future__ import annotations

from model import Direction, ScenarioKind, TradePlan
from model_flow import FlowSignalBar
from model_flow_diagnostic import DiagnosticAggressorFlowRouter


class TargetLadderAggressorFlowRouter(DiagnosticAggressorFlowRouter):
    def _build_plan(
        self,
        episode,
        bar: FlowSignalBar,
        atr: float,
        age: int,
    ) -> TradePlan | None:
        entry = bar.close
        buffer = self.config.stop_buffer_atr * atr
        if episode.direction is Direction.LONG:
            raw_stop = min(episode.extreme, episode.liquidity_level) - buffer
            minimum_stop = entry - self.config.minimum_stop_atr * atr
            stop = min(raw_stop, minimum_stop)
            risk = entry - stop
        else:
            raw_stop = max(episode.extreme, episode.liquidity_level) + buffer
            minimum_stop = entry + self.config.minimum_stop_atr * atr
            stop = max(raw_stop, minimum_stop)
            risk = stop - entry
        risk_atr = risk / atr if atr > 0.0 else 0.0
        common = {
            "entry": entry,
            "stop": stop,
            "risk": risk,
            "risk_atr": risk_atr,
            "atr": atr,
            "sweep_extreme": episode.extreme,
            "liquidity_level": episode.liquidity_level,
            "opposing_internal": episode.opposing_internal,
            "opposing_external": episode.opposing_external,
            "minimum_stop_atr": self.config.minimum_stop_atr,
            "maximum_stop_atr": self.config.maximum_stop_atr,
            "minimum_rr": self.config.minimum_rr,
        }
        if risk <= 0.0:
            self._geometry_diagnostic = {**common, "geometry_reason": "NONPOSITIVE_RISK"}
            return None
        if risk_atr < self.config.minimum_stop_atr:
            self._geometry_diagnostic = {**common, "geometry_reason": "STOP_TOO_TIGHT"}
            return None
        if risk_atr > self.config.maximum_stop_atr:
            self._geometry_diagnostic = {**common, "geometry_reason": "STOP_TOO_WIDE"}
            return None

        ordered: list[tuple[str, float, float]] = []
        for label, level in (
            ("INTERNAL", episode.opposing_internal),
            ("EXTERNAL", episode.opposing_external),
        ):
            favorable = (
                level > entry
                if episode.direction is Direction.LONG
                else level < entry
            )
            if favorable:
                ordered.append((label, level, abs(level - entry) / risk))
        if not ordered:
            self._geometry_diagnostic = {
                **common,
                "geometry_reason": "NO_OPPOSING_LIQUIDITY",
            }
            return None

        selected: tuple[str, float, float] | None = None
        for candidate in ordered:
            if candidate[2] >= self.config.minimum_rr:
                selected = candidate
                break
        if selected is None:
            self._geometry_diagnostic = {
                **common,
                "geometry_reason": "TARGET_LADDER_BELOW_MINIMUM",
                "target_ladder": [
                    {"label": label, "level": level, "rr": rr}
                    for label, level, rr in ordered
                ],
            }
            return None

        label, target_level, uncapped_rr = selected
        self._geometry_diagnostic = {
            **common,
            "geometry_reason": "ACCEPTED",
            "selected_target_label": label,
            "target_level": target_level,
            "uncapped_target_rr": uncapped_rr,
            "target_ladder": [
                {"label": item_label, "level": level, "rr": rr}
                for item_label, level, rr in ordered
            ],
        }
        target_rr = min(uncapped_rr, self.config.maximum_target_rr)
        target = (
            entry + risk * target_rr
            if episode.direction is Direction.LONG
            else entry - risk * target_rr
        )
        return TradePlan(
            scenario_id=episode.scenario_id,
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=episode.direction,
            observed_time_ns=bar.ts_event_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=episode.liquidity_level,
            expected_rr=target_rr,
            details={
                "atr": atr,
                "route_age_bars": age,
                "opposing_internal": episode.opposing_internal,
                "opposing_external": episode.opposing_external,
                "selected_target_label": label,
                "selected_target_level": target_level,
                "pool_formed_ns": episode.liquidity_formed_ns,
                "confirmation_imbalance": bar.imbalance,
            },
        )


__all__ = ["TargetLadderAggressorFlowRouter"]
