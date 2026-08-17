"""Causal ML arbitration for the complete EasyChart RE1 policy.

EasyChart still creates every trade plan and fixes entry, invalidation and
objective before submission. ML3 is a meta-label/router only: at the complete
four-symbol one-minute watermark it estimates target-before-stop probability.
A plan must be more likely to win than lose and must retain positive post-cost
account-risk expectancy. Simultaneous plans are ordered by win likelihood first;
a larger distant target cannot buy priority over a higher-probability plan.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from contracts_v5 import V5TradePlan
from domain import Side
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
from ml3_meta_model import ML3MetaModel
from ml3_online_features import (
    CausalOHLCVState,
    FeatureUnavailable,
    MinuteBar,
    REQUIRED_SYMBOLS,
)
from ml3_router import ScoredPlan, rank_scored_plans
from nautilus_trader.model.identifiers import InstrumentId


ML3_MODEL_ENV = "EASYCHART_RE1_ML3_MODEL"
ML3_TARGET_SLIPPAGE_TICKS = 1
ML3_QUALITY_PROBABILITY = 0.5
ML3_ROUTING_POLICY = (
    "COMPLETE_EASYCHART_PLAN_THEN_CAUSAL_TARGET_BEFORE_STOP_PROBABILITY;"
    "TARGET_MUST_BE_MORE_LIKELY_THAN_STOP_AND_COST_ADJUSTED_EXPECTED_ACCOUNT_R_MUST_BE_POSITIVE;"
    "HIGHEST_TARGET_FIRST_PROBABILITY_THEN_EXPECTED_ACCOUNT_R_WINS_THE_SINGLE_GLOBAL_SLOT"
)


class EasyChartRE1ML3Strategy(EasyChartRE1LocalAuctionStrategy):
    """One continuous four-symbol account with causal ML meta-routing."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        raw_path = os.environ.get(ML3_MODEL_ENV)
        if not raw_path:
            raise RuntimeError(
                f"{ML3_MODEL_ENV} is required; ML3 never falls back to deterministic routing"
            )
        self.ml3_model_path = Path(raw_path).expanduser().resolve()
        self.ml3_model = ML3MetaModel.load(self.ml3_model_path)
        self.ml3_state = CausalOHLCVState(REQUIRED_SYMBOLS)
        self.ml3_counts: dict[str, int] = {}

    def _minc(self, key: str) -> None:
        self.ml3_counts[key] = self.ml3_counts.get(key, 0) + 1

    def on_start(self) -> None:
        super().on_start()
        present = tuple(sorted(self.factor_symbols.values()))
        if present != tuple(sorted(REQUIRED_SYMBOLS)):
            raise RuntimeError(
                f"ML3 requires exactly {REQUIRED_SYMBOLS}; received {present}"
            )
        self._record(
            "ml3_model_loaded",
            model_path=str(self.ml3_model_path),
            model_sha256=self.ml3_model.sha256,
            feature_count=self.ml3_model.feature_count,
            quality_probability=ML3_QUALITY_PROBABILITY,
            routing_policy=ML3_ROUTING_POLICY,
        )

    def _observe_ml3_state(self) -> None:
        one_minute = {
            instrument_id: bar
            for instrument_id, timeframe, bar in self.bar_bucket
            if timeframe == self.EXECUTION_MINUTES
        }
        if len(one_minute) != len(self.config.instrument_ids):
            raise RuntimeError(
                "ML3 cannot advance without one completed one-minute bar for every symbol"
            )
        bars: dict[str, MinuteBar] = {}
        for instrument_id, bar in one_minute.items():
            symbol = self.factor_symbols[instrument_id]
            bars[symbol] = MinuteBar(
                ts_ns=int(bar.ts_event),
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            )
        prior_resets = dict(self.ml3_state.gap_resets)
        self.ml3_state.observe_synchronized(bars)
        changed = {
            symbol: self.ml3_state.gap_resets[symbol] - prior_resets[symbol]
            for symbol in REQUIRED_SYMBOLS
            if self.ml3_state.gap_resets[symbol] != prior_resets[symbol]
        }
        if changed:
            self._record(
                "ml3_contiguous_history_reset",
                event_time_ns=int(self.ml3_state.watermark_ns or 0),
                resets=changed,
            )
            self._minc("contiguous_history_reset")

    @staticmethod
    def _side_sign(plan: V5TradePlan) -> float:
        return 1.0 if plan.side is Side.LONG else -1.0

    def _plan_economics(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> tuple[float, float, float, float]:
        instrument = self.instruments[instrument_id]
        sign = self._side_sign(plan)
        entry = float(plan.entry)
        stop = float(plan.stop)
        target = float(plan.target)
        risk = abs(entry - stop)
        if risk <= 0.0:
            raise FeatureUnavailable("ML3 plan has nonpositive risk")
        tick = float(instrument.price_increment)
        actual_entry = entry + sign * int(self.config.estimated_entry_slippage_ticks) * tick
        actual_target = target - sign * ML3_TARGET_SLIPPAGE_TICKS * tick
        actual_stop = stop - sign * int(self.config.estimated_stop_slippage_ticks) * tick
        entry_fee = float(self.config.estimated_entry_fee_rate) * abs(actual_entry)
        target_fee = float(self.config.estimated_stop_fee_rate) * abs(actual_target)
        stop_fee = float(self.config.estimated_stop_fee_rate) * abs(actual_stop)
        funding = float(self.config.estimated_funding_rate) * abs(actual_entry)
        target_net_r = (
            sign * (actual_target - actual_entry) - entry_fee - target_fee - funding
        ) / risk
        stop_net_r = (
            sign * (actual_stop - actual_entry) - entry_fee - stop_fee - funding
        ) / risk
        if target_net_r <= 0.0 or stop_net_r >= 0.0:
            raise FeatureUnavailable(
                "ML3 plan has unusable post-cost target/stop economics"
            )
        target_account_r = target_net_r / abs(stop_net_r)
        break_even_probability = 1.0 / (1.0 + target_account_r)
        return target_net_r, stop_net_r, target_account_r, break_even_probability

    @staticmethod
    def _feature_digest(features: dict[str, float | str]) -> tuple[str, str]:
        text = json.dumps(
            features,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest(), text

    def _score_plan(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> ScoredPlan | None:
        if self.ml3_state.watermark_ns != int(plan.observed_time_ns):
            self._minc("plan_rejected_watermark_mismatch")
            self._record(
                "ml3_plan_rejected_watermark_mismatch",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                plan_observed_time_ns=int(plan.observed_time_ns),
                ml3_watermark_ns=self.ml3_state.watermark_ns,
            )
            return None
        try:
            features = self.ml3_state.plan_features(plan)
            probability = self.ml3_model.predict_probability(features)
            target_net_r, stop_net_r, target_account_r, break_even = self._plan_economics(
                instrument_id,
                plan,
            )
        except (FeatureUnavailable, ValueError, KeyError) as exc:
            self._minc("plan_rejected_feature_unavailable")
            self._record(
                "ml3_plan_rejected_feature_unavailable",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                reason=f"{type(exc).__name__}:{exc}",
                model_sha256=self.ml3_model.sha256,
            )
            return None
        expected_account_r = probability * target_account_r - (1.0 - probability)
        feature_sha256, feature_json = self._feature_digest(features)
        self._record(
            "ml3_plan_scored",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            target_first_probability=probability,
            quality_probability=ML3_QUALITY_PROBABILITY,
            break_even_probability=break_even,
            probability_edge=probability - break_even,
            target_net_r=target_net_r,
            stop_net_r=stop_net_r,
            target_account_r=target_account_r,
            expected_account_r=expected_account_r,
            feature_sha256=feature_sha256,
            feature_json=feature_json,
            model_sha256=self.ml3_model.sha256,
            routing_policy=ML3_ROUTING_POLICY,
        )
        self._minc("plan_scored")
        if probability <= ML3_QUALITY_PROBABILITY:
            self._minc("plan_rejected_target_not_more_likely")
            self._record(
                "ml3_plan_rejected_target_not_more_likely",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                target_first_probability=probability,
                quality_probability=ML3_QUALITY_PROBABILITY,
                break_even_probability=break_even,
                expected_account_r=expected_account_r,
            )
            return None
        if not math.isfinite(expected_account_r) or expected_account_r <= 0.0:
            self._minc("plan_rejected_nonpositive_expectancy")
            self._record(
                "ml3_plan_rejected_nonpositive_expectancy",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                target_first_probability=probability,
                break_even_probability=break_even,
                expected_account_r=expected_account_r,
            )
            return None
        self._minc("plan_quality_and_expectancy_accepted")
        return ScoredPlan(
            instrument_id=instrument_id,
            plan=plan,
            target_first_probability=probability,
            target_net_r=target_net_r,
            stop_net_r=stop_net_r,
            expected_net_r=expected_account_r,
        )

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        self._observe_common_factor()
        self._observe_ml3_state()

        scored: list[ScoredPlan] = []
        for instrument_id, timeframe, bar in sorted(
            self.bar_bucket,
            key=lambda item: (-item[1], str(item[0])),
        ):
            engine = self.scenario_engines[instrument_id]
            emitted = engine.on_bar(timeframe, self._candle(bar))
            for transition in engine.drain_trace():
                if transition.get("event_time_ns", 0) >= self.config.trading_start_ns:
                    self._record(
                        "scenario_transition",
                        instrument_id=str(instrument_id),
                        timeframe_minutes=timeframe,
                        **transition,
                    )
            if bar.ts_event < self.config.trading_start_ns:
                continue
            for plan in emitted:
                self.plan_log[plan.plan_id] = plan
                self._record("plan", **self._plan_event_values(plan))
                if not self._factor_allows(plan):
                    continue
                candidate = self._score_plan(instrument_id, plan)
                if candidate is not None:
                    scored.append(candidate)

        ranked = rank_scored_plans(scored)
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, item in enumerate(ranked, start=1):
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=item.plan.plan_id,
                        instrument_id=str(item.instrument_id),
                        arbitration_rank=rank,
                        target_first_probability=item.target_first_probability,
                        expected_account_r=item.expected_net_r,
                        active_plan_id=None
                        if self.active_plan is None
                        else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                    )
            else:
                selected_index: int | None = None
                for index, item in enumerate(ranked):
                    if self._submit_plan(item.instrument_id, item.plan):
                        selected_index = index
                        self._minc("arbitration_selected")
                        self._record(
                            "arbitration_selected",
                            plan_id=item.plan.plan_id,
                            instrument_id=str(item.instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                            target_first_probability=item.target_first_probability,
                            expected_account_r=item.expected_net_r,
                            arbitration_policy=ML3_ROUTING_POLICY,
                        )
                        break
                if selected_index is not None:
                    selected = ranked[selected_index]
                    for rank, item in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "plan_skipped_arbitration",
                            plan_id=item.plan.plan_id,
                            instrument_id=str(item.instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected.plan.plan_id,
                            target_first_probability=item.target_first_probability,
                            expected_account_r=item.expected_net_r,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None

    @property
    def ml3_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self.ml3_counts.items())),
            "model_path": str(self.ml3_model_path),
            "model_sha256": self.ml3_model.sha256,
            "feature_count": self.ml3_model.feature_count,
            "quality_probability": ML3_QUALITY_PROBABILITY,
            "watermark_ns": self.ml3_state.watermark_ns,
            "gap_resets": dict(self.ml3_state.gap_resets),
            "routing_policy": ML3_ROUTING_POLICY,
        }


__all__ = [
    "EasyChartRE1ML3Strategy",
    "ML3_MODEL_ENV",
    "ML3_QUALITY_PROBABILITY",
    "ML3_ROUTING_POLICY",
    "ML3_TARGET_SLIPPAGE_TICKS",
]
