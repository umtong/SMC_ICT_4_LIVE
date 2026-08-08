"""Candidate 16 v3: actual best-quote resiliency and later initiative.

The v2 temporal state machine is preserved.  Only its coarse distance-band depth
observations are replaced with Candidate 03-derived Binance bookTicker facts:

- end-of-minute best-quote imbalance;
- event-ordered best-quote defense (+1), withdrawal (-1), or disagreement (0);
- completed-minute midpoint return for the later initiative.

No v1/v2 PnL threshold, entry geometry, stop, target, risk, or holding rule is
changed.
"""
from __future__ import annotations

from typing import Any

from strategy_base import PendingSetup
from strategy_v2 import Candidate16V2Config
from strategy_v2 import Candidate16V2Strategy


class Candidate16V3Config(Candidate16V2Config, frozen=True):
    pass


class Candidate16V3Strategy(Candidate16V2Strategy):
    """Use actual best bid/ask recovery as the independent state channel."""

    _FEATURE_ALIASES = {
        # The inherited v2 state machine expects a signed book imbalance and a
        # signed response on the liquidity side ahead of each direction.
        "depth_imbalance_1": "topbook_quote_imbalance_end",
        "bid_depth_change_1_1m": "topbook_bid_queue_response",
        "ask_depth_change_1_1m": "topbook_ask_queue_response",
        "depth_snapshot_age_seconds": "topbook_last_quote_age_seconds",
        # A later initiative requires midpoint progress, not another candle-body
        # restatement. Aggressor flow remains the independent trade channel.
        "ret_60s_bps": "topbook_mid_ret_60s_bps",
    }

    def __init__(self, config: Candidate16V3Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate16_v3_topbook_parent_observations": 0,
                "candidate16_v3_topbook_ready_observations": 0,
                "candidate16_v3_bid_defense_observations": 0,
                "candidate16_v3_ask_defense_observations": 0,
                "candidate16_v3_bid_withdrawal_observations": 0,
                "candidate16_v3_ask_withdrawal_observations": 0,
                "candidate16_v3_book_disagreement_observations": 0,
            },
        )

    def _raw_feature(self, name: str) -> float:
        return super()._feature(name)

    def _feature(self, name: str) -> float:
        return super()._feature(self._FEATURE_ALIASES.get(name, name))

    def _accumulate_displayed_state(
        self,
        setup: PendingSetup,
        direction: int,
    ) -> None:
        super()._accumulate_displayed_state(setup, direction)
        bid_response = self._raw_feature("topbook_bid_queue_response")
        ask_response = self._raw_feature("topbook_ask_queue_response")
        self.diagnostics["candidate16_v3_topbook_parent_observations"] = int(
            self.diagnostics["candidate16_v3_topbook_parent_observations"],
        ) + 1
        if self.current_feature is not None and bool(
            self.current_feature.get("topbook_feature_ready", False),
        ):
            self.diagnostics["candidate16_v3_topbook_ready_observations"] = int(
                self.diagnostics["candidate16_v3_topbook_ready_observations"],
            ) + 1
        if bid_response > 0.0:
            self.diagnostics["candidate16_v3_bid_defense_observations"] = int(
                self.diagnostics["candidate16_v3_bid_defense_observations"],
            ) + 1
        elif bid_response < 0.0:
            self.diagnostics["candidate16_v3_bid_withdrawal_observations"] = int(
                self.diagnostics["candidate16_v3_bid_withdrawal_observations"],
            ) + 1
        if ask_response > 0.0:
            self.diagnostics["candidate16_v3_ask_defense_observations"] = int(
                self.diagnostics["candidate16_v3_ask_defense_observations"],
            ) + 1
        elif ask_response < 0.0:
            self.diagnostics["candidate16_v3_ask_withdrawal_observations"] = int(
                self.diagnostics["candidate16_v3_ask_withdrawal_observations"],
            ) + 1
        if bid_response == 0.0 and ask_response == 0.0:
            self.diagnostics["candidate16_v3_book_disagreement_observations"] = int(
                self.diagnostics["candidate16_v3_book_disagreement_observations"],
            ) + 1

        setup.details["latest_topbook_resiliency"] = {
            "parent_direction": direction,
            "quote_updates": self._raw_feature("topbook_quote_updates"),
            "quote_imbalance_end": self._raw_feature(
                "topbook_quote_imbalance_end",
            ),
            "bid_queue_response": bid_response,
            "ask_queue_response": ask_response,
            "bid_persistent_refill": self._raw_feature(
                "topbook_bid_persistent_refill",
            ),
            "ask_persistent_refill": self._raw_feature(
                "topbook_ask_persistent_refill",
            ),
            "mid_ret_60s_bps": self._raw_feature(
                "topbook_mid_ret_60s_bps",
            ),
            "mid_efficiency_60s": self._raw_feature(
                "topbook_mid_efficiency_60s",
            ),
            "spread_start_bps": self._raw_feature(
                "topbook_spread_start_bps",
            ),
            "spread_end_bps": self._raw_feature(
                "topbook_spread_end_bps",
            ),
            "spread_max_bps": self._raw_feature(
                "topbook_spread_max_bps",
            ),
            "last_quote_age_seconds": self._raw_feature(
                "topbook_last_quote_age_seconds",
            ),
        }


__all__ = ["Candidate16V3Config", "Candidate16V3Strategy"]
