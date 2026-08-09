"""Long-only ablation of Candidate 47's public NFI squeeze-release family.

This removes exactly one causal branch: downside releases are observed and
counted but never compete for the global entry slot.  Long detection, structural
coil stop, measured-move target, midline invalidation, costs, current-NAV 3%
risk sizing and NautilusTrader execution remain unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

import squeeze_release_strategy as _squeeze

Candidate47SqueezeLongConfig = _squeeze.Candidate47SqueezeConfig
Candidate35Config = Candidate47SqueezeLongConfig
Candidate35StrategyBase = _squeeze.Candidate47SqueezeReleaseStrategy
SYMBOLS = _squeeze.SYMBOLS
_router = _squeeze._router


class Candidate47SqueezeReleaseLongOnlyStrategy(Candidate35StrategyBase):
    """Exact squeeze-release policy with the empirically failed short side removed."""

    def __init__(self, config: Candidate47SqueezeLongConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "squeeze_long_candidates": 0,
                "squeeze_short_observations_rejected": 0,
                "squeeze_long_only_policy": True,
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), len(open_symbols)
        )
        if len(open_symbols) > 1:
            self.diagnostics["global_position_violations"] += 1
            for symbol in open_symbols:
                self.cancel_all_orders(self.instrument_ids[symbol])
                self.close_all_positions(self.instrument_ids[symbol])
            return
        if open_symbols:
            self.current_symbol = open_symbols[0]
            self._manage_open_position(ts_event)
            return

        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="SQUEEZE_LONG_MARKET_PARENT_NOT_FILLED",
                )
                self._clear_trade_state()
            return

        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        if any(len(self.bars[symbol]) < 220 for symbol in SYMBOLS):
            return

        self.diagnostics["squeeze_five_minute_decisions"] += 1
        candidates: list[tuple[float, str, _squeeze.SqueezeReleaseState]] = []
        for symbol in SYMBOLS:
            five = _squeeze.aggregate_five_minute(tuple(self.bars[symbol]))
            if not five:
                continue
            state = _squeeze.causal_squeeze_release(
                five,
                current_ts=five[-1].ts_event,
            )
            if not state.ready:
                continue
            self.diagnostics["squeeze_ready_symbol_states"] += 1
            if state.side < 0:
                self.diagnostics["squeeze_short_observations_rejected"] += 1
                continue
            if state.side > 0:
                self.diagnostics["squeeze_release_candidates"] += 1
                self.diagnostics["squeeze_long_candidates"] += 1
                candidates.append((state.score, symbol, state))

        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        candidates.sort(
            key=lambda item: (item[0], item[1] == "BTCUSDT", item[1]),
            reverse=True,
        )
        score, symbol, state = candidates[0]
        episode_id = f"{symbol}:{state.ts_event}:NFI_SQUEEZE_RELEASE_LONG"
        if episode_id in self._seen_episodes:
            self.diagnostics["squeeze_duplicate_episode_rejections"] += 1
            return

        entry = float(self.bars[symbol][-1].close)
        if not (state.stop < entry < state.target):
            self.diagnostics["squeeze_consumed_geometry_rejections"] += 1
            return

        decision = _router.RouteDecision(
            symbol=symbol,
            state="NFI_SQUEEZE_RELEASE_LONG",
            side=1,
            score=score,
            expected_target_r=state.reward_risk,
            atr=math.nan,
            entry_reference=entry,
            stop_reference=state.stop,
            objective_reference=state.target,
            episode_ts=state.ts_event,
            reasons=(
                "PRIOR_24_BAR_COMPRESSION",
                "SQUEEZE_STATE_RELEASED",
                "COMPLETED_5M_CLOSE_ABOVE_BOLLINGER_BAND",
                "PUBLIC_NFI_166_MECHANISM",
                "LONG_ONLY_ABLATION",
            ),
            diagnostics={
                "causal_episode_id": episode_id,
                "squeeze_previous": state.squeeze_previous,
                "squeeze_current": state.squeeze_current,
                "prior_squeeze_count": state.prior_squeeze_count,
                "bb_upper": state.bb_upper,
                "bb_middle": state.bb_middle,
                "bb_lower": state.bb_lower,
                "kc_upper": state.kc_upper,
                "kc_lower": state.kc_lower,
                "coil_high": state.coil_high,
                "coil_low": state.coil_low,
                "measured_move_target": state.target,
                "reward_risk": state.reward_risk,
                "short_branch_enabled": False,
            },
        )
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) == before:
            return
        self._seen_episodes.add(episode_id)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "scenario_id": f"c47-squeeze-long-{before + 1:07d}",
                    "candidate": "candidate-47-public-nfi-squeeze-release-long-only",
                    "causal_episode_id": episode_id,
                    "entry_policy": "MARKET_BRACKET_AFTER_COMPLETED_5M_RELEASE",
                    "dynamic_invalidation": "5M_CLOSE_THROUGH_BOLLINGER_MIDLINE",
                    "short_branch_enabled": False,
                }
            )


Candidate35Strategy = Candidate47SqueezeReleaseLongOnlyStrategy
