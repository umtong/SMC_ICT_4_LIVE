"""ZaratustraV13 short continuation routed by derivatives sponsorship.

This is a structural reuse, not a new indicator stack.  It keeps the public
ZaratustraV13 5-minute short trigger, the existing one-account NautilusTrader
execution shell, 3% current-NAV planned-loss sizing, source stop, and causal
1-minute trailing implementation.  It repairs two mismatches exposed by the
continuous-account evidence:

1. A short entry during falling open interest is frequently a late liquidation
   move rather than fresh downside sponsorship.  Continuation therefore
   requires completed-minute OI expansion, falling perpetual premium, and
   persistent sell aggression.
2. The inherited source adapter allowed positions to remain open for days even
   though the project is a daytrading system and the source's reported average
   duration was under three hours.  The policy is invalidated after one eight-
   hour perpetual funding cycle (480 completed minutes).

Repeated 5-minute level signals owned by the same completed 60-minute swing
high are collapsed into one causal episode.  ``baseline_daytrade`` is retained
only as a causal ablation; ``sponsored_daytrade`` is the frozen research policy.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import math
from pathlib import Path
import sys
from typing import NamedTuple

import numpy as np
import pandas as pd

from router import FeatureObservation, route_universe
from strategy_base import SYMBOLS


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v13.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v13_sponsored_short_base",
    _BASE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V13 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v13_sponsorship_mode: str = "sponsored_daytrade"
    v13_episode_lookback_minutes: int = 60


class _SponsorshipObservation(NamedTuple):
    observed_time_ns: int
    ready: bool
    premium_change_5m: float
    flow_3m: float
    oi_change_15m: float


class _SponsorshipFeatureStore:
    def __init__(self, path: Path) -> None:
        frame = pd.read_csv(
            path,
            compression="infer",
            usecols=[
                "observed_time_ns",
                "feature_ready",
                "premium_change_5m",
                "flow_3m",
                "oi_change_15m",
            ],
        )
        self.times = (
            pd.to_numeric(frame["observed_time_ns"], errors="raise")
            .astype("int64")
            .to_numpy(copy=True)
        )
        if self.times.size == 0 or np.any(np.diff(self.times) <= 0):
            raise RuntimeError(f"invalid sponsorship feature clock: {path}")
        self.ready = (
            frame["feature_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )
        self.premium = pd.to_numeric(
            frame["premium_change_5m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.flow = pd.to_numeric(
            frame["flow_3m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.oi = pd.to_numeric(
            frame["oi_change_15m"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)

    def observation(
        self,
        ts_event: int,
        max_age_seconds: float,
    ) -> _SponsorshipObservation:
        index = int(np.searchsorted(self.times, ts_event, side="right") - 1)
        if index < 0:
            return _SponsorshipObservation(
                0,
                False,
                math.nan,
                math.nan,
                math.nan,
            )
        observed = int(self.times[index])
        age = (int(ts_event) - observed) / 1_000_000_000.0
        if age < -1e-9:
            raise RuntimeError("future sponsorship feature reached strategy")
        premium = float(self.premium[index])
        flow = float(self.flow[index])
        oi = float(self.oi[index])
        ready = (
            age <= float(max_age_seconds)
            and bool(self.ready[index])
            and math.isfinite(premium)
            and math.isfinite(flow)
            and math.isfinite(oi)
        )
        return _SponsorshipObservation(
            observed_time_ns=observed,
            ready=ready,
            premium_change_5m=premium if ready else math.nan,
            flow_3m=flow if ready else math.nan,
            oi_change_15m=oi if ready else math.nan,
        )


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """One-slot, eight-hour short continuation specialist."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v13_sponsorship_mode).strip().lower()
        if mode not in {"baseline_daytrade", "sponsored_daytrade"}:
            raise ValueError(f"unsupported V13 sponsorship mode: {mode}")
        if str(config.zaratustra_variant).strip().lower() != "source_short":
            raise ValueError("V13 sponsored policy requires source_short")
        if int(config.v13_episode_lookback_minutes) != 60:
            raise ValueError("frozen causal episode lookback is 60 minutes")
        if int(config.max_hold_minutes) != 480:
            raise ValueError("frozen daytrade invalidation is 480 minutes")
        self._sponsorship_mode = mode
        self._sponsorship_features: dict[str, _SponsorshipFeatureStore] = {}
        self._used_swing_episodes: set[tuple[str, int, int]] = set()
        self.diagnostics.update(
            {
                "candidate": "candidate-55-v13-sponsored-short-v1",
                "v13_sponsorship_mode": mode,
                "daytrade_max_hold_minutes": 480,
                "causal_episode_lookback_minutes": 60,
                "raw_v13_short_candidates": 0,
                "sponsorship_feature_stale": 0,
                "oi_build_rejections": 0,
                "premium_down_rejections": 0,
                "persistent_sell_flow_rejections": 0,
                "sponsored_short_candidates": 0,
                "causal_episode_rejections": 0,
                "sponsored_short_selected": 0,
                "sponsorship_candidate_records": [],
                "selected_swing_episode_keys": [],
                "zero_boundaries_are_economic_not_fitted": 1,
                "one_global_slot": 1,
            }
        )

    def on_start(self) -> None:
        super().on_start()
        self._sponsorship_features = {
            symbol: _SponsorshipFeatureStore(path)
            for symbol, path in self.feature_paths.items()
        }

    def _swing_episode_key(self, symbol: str, side: int) -> tuple[str, int, int]:
        lookback = int(self.config.v13_episode_lookback_minutes)
        window = list(self.bars[symbol])[-(lookback + 1):]
        if not window:
            return symbol, side, 0
        if side < 0:
            anchor = max(
                window,
                key=lambda bar: (float(bar.high), -int(bar.ts_event)),
            )
        else:
            anchor = min(
                window,
                key=lambda bar: (float(bar.low), int(bar.ts_event)),
            )
        return symbol, side, int(anchor.ts_event)

    def _filter_decisions(self, decisions, ts_event: int):
        candidates = []
        for decision in decisions.values():
            if not decision.actionable or int(decision.side) != -1:
                continue
            self.diagnostics["raw_v13_short_candidates"] += 1
            episode_key = self._swing_episode_key(
                decision.symbol,
                int(decision.side),
            )
            if episode_key in self._used_swing_episodes:
                self.diagnostics["causal_episode_rejections"] += 1
                continue

            diagnostics = dict(decision.diagnostics)
            if self._sponsorship_mode == "baseline_daytrade":
                diagnostics.update(
                    {
                        "v13_sponsorship_mode": self._sponsorship_mode,
                        "swing_episode_anchor_ts": int(episode_key[2]),
                    }
                )
                candidates.append(
                    (replace(decision, diagnostics=diagnostics), episode_key)
                )
                continue

            observation = self._sponsorship_features[
                decision.symbol
            ].observation(
                ts_event,
                self.config.feature_max_age_seconds,
            )
            accepted = False
            rejection = ""
            if not observation.ready:
                self.diagnostics["sponsorship_feature_stale"] += 1
                rejection = "FEATURE_NOT_READY"
            elif float(observation.oi_change_15m) <= 0.0:
                self.diagnostics["oi_build_rejections"] += 1
                rejection = "OPEN_INTEREST_NOT_BUILDING"
            elif float(observation.premium_change_5m) >= 0.0:
                self.diagnostics["premium_down_rejections"] += 1
                rejection = "PERPETUAL_PREMIUM_NOT_FALLING"
            elif float(observation.flow_3m) >= 0.0:
                self.diagnostics["persistent_sell_flow_rejections"] += 1
                rejection = "PERSISTENT_SELL_AGGRESSION_ABSENT"
            else:
                accepted = True
                self.diagnostics["sponsored_short_candidates"] += 1

            record = {
                "ts_event": int(ts_event),
                "symbol": decision.symbol,
                "episode_ts": int(decision.episode_ts),
                "swing_episode_anchor_ts": int(episode_key[2]),
                "eligible": int(accepted),
                "rejection": rejection,
                "observed_time_ns": int(observation.observed_time_ns),
                "feature_ready": int(observation.ready),
                "oi_change_15m": observation.oi_change_15m,
                "premium_change_5m": observation.premium_change_5m,
                "flow_3m": observation.flow_3m,
                "source_score": float(decision.score),
                "source_tag": diagnostics.get("source_tag"),
            }
            self.diagnostics["sponsorship_candidate_records"].append(record)
            self._event("V13_SPONSORSHIP_CANDIDATE", ts_event, **record)
            if not accepted:
                continue

            diagnostics.update(
                {
                    "derivative_state": "NEW_SHORT_SPONSORED_DOWNSIDE",
                    "sponsorship_observed_time_ns": int(
                        observation.observed_time_ns
                    ),
                    "signal_oi_change_15m": float(
                        observation.oi_change_15m
                    ),
                    "signal_premium_change_5m": float(
                        observation.premium_change_5m
                    ),
                    "signal_flow_3m": float(observation.flow_3m),
                    "v13_sponsorship_mode": self._sponsorship_mode,
                    "swing_episode_anchor_ts": int(episode_key[2]),
                }
            )
            candidates.append(
                (replace(decision, diagnostics=diagnostics), episode_key)
            )
        return candidates

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]),
            len(open_symbols),
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
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if self.minute_index - self.entry_pending_minute > 2:
                if self.current_symbol is not None:
                    self.cancel_all_orders(
                        self.instrument_ids[self.current_symbol]
                    )
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return
        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return

        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000,
            tz=timezone.utc,
        )
        if moment.minute % 5 != 4:
            return
        required_minutes = max(
            int(self.config.zaratustra_startup_30m_candles) * 30,
            int(self.config.v13_episode_lookback_minutes) + 1,
        )
        if any(
            len(self.bars[symbol]) < required_minutes for symbol in SYMBOLS
        ):
            return

        features = {
            symbol: FeatureObservation(
                int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS
            },
            features_by_symbol=features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(
                    family_counts.get(decision.state, 0)
                ) + 1
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(
                        reason_counts.get(reason, 0)
                    ) + 1

        filtered = self._filter_decisions(decisions, ts_event)
        if not filtered:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if self._sponsorship_mode == "sponsored_daytrade":
            filtered.sort(
                key=lambda item: (
                    float(
                        item[0].diagnostics[
                            "signal_premium_change_5m"
                        ]
                    ),
                    -float(
                        item[0].diagnostics["signal_oi_change_15m"]
                    ),
                    float(item[0].diagnostics["signal_flow_3m"]),
                    -float(item[0].score),
                    item[0].symbol,
                    int(item[0].episode_ts),
                )
            )
        else:
            filtered.sort(
                key=lambda item: (
                    -float(item[0].score),
                    item[0].symbol,
                    int(item[0].episode_ts),
                )
            )
        winner, episode_key = filtered[0]

        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if (
            self.minute_index - self.last_entry_minute
            < self.config.cooldown_minutes
        ):
            self.diagnostics["cooldown_rejections"] += 1
            return

        self._trail_active = False
        self._trail_best = None
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before:
            return

        self._used_swing_episodes.add(episode_key)
        episode_text = (
            f"{episode_key[0]}:{episode_key[1]}:{episode_key[2]}"
        )
        self.diagnostics["selected_swing_episode_keys"].append(episode_text)
        if self._sponsorship_mode == "sponsored_daytrade":
            self.diagnostics["sponsored_short_selected"] += 1
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-v13-sponsored-short-v1",
                    "source_variant": "source_short",
                    "v13_sponsorship_mode": self._sponsorship_mode,
                    "derivative_state": (
                        "NEW_SHORT_SPONSORED_DOWNSIDE"
                        if self._sponsorship_mode == "sponsored_daytrade"
                        else "BASELINE_UNROUTED_SHORT"
                    ),
                    "swing_episode_key": episode_text,
                    "causal_episode_definition": (
                        "same-symbol short signals sharing completed 60m swing high"
                    ),
                    "management": (
                        "source stop and causal trailing; thesis stale at 480 minutes"
                    ),
                    "daytrade_max_hold_minutes": 480,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
