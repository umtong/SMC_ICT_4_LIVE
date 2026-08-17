"""Period-robust account arbitration for the ML3v3 opportunity union."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts_v5 import V5TradePlan
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
from features_ml3v3 import (
    FEATURE_NAMES,
    ML3V3FeatureBook,
    build_ml3v3_features,
)
from ml1_model import TradeEconomics, estimate_trade_economics
from nautilus_trader.model.identifiers import InstrumentId

from robust_ensemble import PeriodRobustEnsemble, RobustScore


@dataclass(frozen=True, slots=True)
class ML3V3RuntimeConfig:
    model_path: Path


_RUNTIME: ML3V3RuntimeConfig | None = None


def configure_ml3v3_runtime(config: ML3V3RuntimeConfig) -> None:
    global _RUNTIME
    _RUNTIME = config


@dataclass(frozen=True, slots=True)
class _ScoredPlan:
    instrument_id: InstrumentId
    plan: V5TradePlan
    features: dict[str, float]
    economics: TradeEconomics
    score: RobustScore


class EasyChartML3V3Strategy(EasyChartRE1LocalAuctionStrategy):
    """One four-symbol account maximizing robust after-cost log-growth rate."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        if _RUNTIME is None:
            raise RuntimeError("ML3v3 runtime was not configured with an ensemble model")
        self.ml3v3_runtime = _RUNTIME
        self.ml3v3_model = PeriodRobustEnsemble.load(self.ml3v3_runtime.model_path)
        self.ml3v3_model.assert_selectable()
        if tuple(self.ml3v3_model.feature_names) != tuple(FEATURE_NAMES):
            raise RuntimeError("ML3v3 ensemble feature schema does not match runtime code")
        self.ml3v3_feature_book = ML3V3FeatureBook()
        self.ml3v3_counts: dict[str, int] = {}

    def _minc(self, key: str) -> None:
        self.ml3v3_counts[key] = self.ml3v3_counts.get(key, 0) + 1

    def _observe_feature_bucket(self) -> None:
        items: list[tuple[str, int, Any]] = []
        for instrument_id, timeframe, bar in self.bar_bucket:
            symbol = self.factor_symbols.get(instrument_id)
            if symbol is None:
                symbol = self.instruments[instrument_id].raw_symbol.value
            items.append((symbol, timeframe, self._candle(bar)))
        self.ml3v3_feature_book.observe_bucket(items)

    def _macro_side(self, instrument_id: InstrumentId) -> Any:
        return getattr(self.scenario_engines[instrument_id], "_macro_side", None)

    def _flow_observation(self, instrument_id: InstrumentId) -> Any:
        analyzer = self.factor_analyzers.get(instrument_id)
        return None if analyzer is None else analyzer.last_observation

    def _economics(self, instrument_id: InstrumentId, plan: V5TradePlan) -> TradeEconomics:
        instrument = self.instruments[instrument_id]
        entry_fee = float(self.config.estimated_entry_fee_rate)
        stop_fee = float(self.config.estimated_stop_fee_rate)
        return estimate_trade_economics(
            side=plan.side,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            tick_size=float(instrument.price_increment),
            entry_fee_rate=entry_fee,
            target_fee_rate=entry_fee,
            stop_fee_rate=stop_fee,
            funding_rate=float(getattr(self.config, "estimated_funding_rate", 0.0)),
            entry_slippage_ticks=int(self.config.estimated_entry_slippage_ticks),
            target_slippage_ticks=0,
            stop_slippage_ticks=int(self.config.estimated_stop_slippage_ticks),
        )

    def _score_plan(self, instrument_id: InstrumentId, plan: V5TradePlan) -> _ScoredPlan:
        features = build_ml3v3_features(
            plan,
            feature_book=self.ml3v3_feature_book,
            macro_side=self._macro_side(instrument_id),
            factor_state=self.factor_state,
            flow_observation=self._flow_observation(instrument_id),
        )
        economics = self._economics(instrument_id, plan)
        score = self.ml3v3_model.score(
            features,
            economics,
            plan,
            risk_fraction=float(self.config.risk_fraction),
        )
        self._record(
            "ml3v3_plan_scored",
            plan_id=plan.plan_id,
            causal_event_id=plan.causal_event_id,
            instrument_id=str(instrument_id),
            symbol=plan.symbol,
            family=plan.family,
            side=plan.side.name,
            scenario_path=plan.scenario_path,
            scale_name=plan.scale_name,
            ensemble_id=self.ml3v3_model.ensemble_id,
            member_probabilities=list(score.member_probabilities),
            probability_median=score.probability_median,
            probability_lower_quartile=score.probability_lower_quartile,
            probability_upper_quartile=score.probability_upper_quartile,
            probability_mad=score.probability_mad,
            robust_target_probability=score.robust_target_probability,
            target_account_r=score.target_account_r,
            expected_account_r=score.expected_account_r,
            expected_log_growth=score.expected_log_growth,
            expected_duration_minutes=score.expected_duration_minutes,
            expected_log_growth_per_hour=score.expected_log_growth_per_hour,
            duration_source=score.duration_source,
            accepted=score.accepted,
            decision_reason=score.reason,
            win_net_r=economics.win_net_r,
            loss_net_r=economics.loss_net_r,
            break_even_probability=economics.break_even_probability,
        )
        self._minc("plans_scored")
        self._minc(f"decision_{score.reason.lower()}")
        return _ScoredPlan(
            instrument_id=instrument_id,
            plan=plan,
            features=features,
            economics=economics,
            score=score,
        )

    @staticmethod
    def _rank(item: _ScoredPlan) -> tuple[Any, ...]:
        plan = item.plan
        return (
            -item.score.expected_log_growth_per_hour,
            -item.score.expected_log_growth,
            -item.score.robust_target_probability,
            -item.score.expected_account_r,
            plan.interaction_time_ns,
            -plan.higher_timeframe_minutes,
            plan.symbol,
            plan.plan_id,
        )

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        self._observe_feature_bucket()
        self._observe_common_factor()

        scored: list[_ScoredPlan] = []
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
                minimum_rr = max(1.0, float(self.config.min_gross_rr))
                if plan.gross_rr + 1e-12 < minimum_rr:
                    self._record(
                        "ml3v3_plan_geometry_rejected",
                        plan_id=plan.plan_id,
                        gross_rr=plan.gross_rr,
                        minimum=minimum_rr,
                    )
                    self._minc("plans_below_minimum_gross_rr")
                    continue
                scored.append(self._score_plan(instrument_id, plan))

        ranked = sorted(
            (item for item in scored if item.score.accepted),
            key=self._rank,
        )
        if scored and not ranked:
            self._minc("bucket_without_positive_robust_growth")
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, item in enumerate(ranked, start=1):
                    self._record(
                        "ml3v3_plan_skipped_global_slot",
                        plan_id=item.plan.plan_id,
                        instrument_id=str(item.instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                        expected_log_growth_per_hour=item.score.expected_log_growth_per_hour,
                        expected_log_growth=item.score.expected_log_growth,
                    )
            else:
                selected_index: int | None = None
                for index, item in enumerate(ranked):
                    if self._submit_plan(item.instrument_id, item.plan):
                        selected_index = index
                        self._minc("arbitration_selected")
                        self._record(
                            "ml3v3_arbitration_selected",
                            plan_id=item.plan.plan_id,
                            causal_event_id=item.plan.causal_event_id,
                            instrument_id=str(item.instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                            scored_candidates=len(scored),
                            ensemble_id=self.ml3v3_model.ensemble_id,
                            robust_target_probability=item.score.robust_target_probability,
                            expected_account_r=item.score.expected_account_r,
                            expected_log_growth=item.score.expected_log_growth,
                            expected_log_growth_per_hour=item.score.expected_log_growth_per_hour,
                            expected_duration_minutes=item.score.expected_duration_minutes,
                        )
                        break
                if selected_index is not None:
                    selected = ranked[selected_index]
                    for rank, item in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "ml3v3_plan_skipped_arbitration",
                            plan_id=item.plan.plan_id,
                            instrument_id=str(item.instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected.plan.plan_id,
                            expected_log_growth_per_hour=item.score.expected_log_growth_per_hour,
                            expected_log_growth=item.score.expected_log_growth,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None

    @property
    def ml3v3_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self.ml3v3_counts.items())),
            "ensemble_id": self.ml3v3_model.ensemble_id,
            "model_path": str(self.ml3v3_runtime.model_path),
            "member_ids": self.ml3v3_model.member_ids,
            "member_windows": self.ml3v3_model.member_windows,
            "feature_count": len(FEATURE_NAMES),
            "probability_quantile": self.ml3v3_model.probability_quantile,
            "decision": "positive_period_robust_after_cost_fixed_risk_log_growth",
            "arbitration": "expected_log_growth_per_expected_hour",
        }


StrategyClass = EasyChartML3V3Strategy
