"""NautilusTrader execution binding for the EasyChart ML1 selector.

The inherited RE1 layer remains responsible for market entry, fixed 3% NAV risk
sizing, reduce-only stop/target protection, fees, fills and one global account
slot.  ML1 changes only candidate selection and arbitration.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts_v5 import V5TradePlan
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
from execution_re1_market_factor import EasyChartRE1MarketFactorStrategy
from nautilus_trader.model.identifiers import InstrumentId

from ml1_features import CausalFeatureBook, FEATURE_NAMES, build_plan_features
from ml1_model import (
    ModelDecision,
    PortableBinaryModel,
    TradeEconomics,
    estimate_trade_economics,
)


@dataclass(frozen=True, slots=True)
class ML1RuntimeConfig:
    mode: str = "shadow"
    model_path: Path = Path(__file__).resolve().parent / "models" / "bootstrap_shadow.json"
    min_probability: float | None = None
    probability_edge: float | None = None
    min_expected_net_r: float | None = None
    target_slippage_ticks: int = 1
    allow_shadow_model: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"shadow", "select"}:
            raise ValueError("ML1 mode must be 'shadow' or 'select'")
        if self.target_slippage_ticks < 0:
            raise ValueError("target_slippage_ticks cannot be negative")
        for name, value in (
            ("min_probability", self.min_probability),
            ("probability_edge", self.probability_edge),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")


_RUNTIME = ML1RuntimeConfig()


def configure_ml1_runtime(config: ML1RuntimeConfig) -> None:
    global _RUNTIME
    _RUNTIME = config


@dataclass(frozen=True, slots=True)
class _ScoredPlan:
    instrument_id: InstrumentId
    plan: V5TradePlan
    features: dict[str, float]
    economics: TradeEconomics
    decision: ModelDecision
    baseline_eligible: bool


class EasyChartML1Strategy(EasyChartRE1LocalAuctionStrategy):
    """Causal candidate meta-selector over the complete RE1 opportunity set."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.ml_runtime = _RUNTIME
        self.ml_model = PortableBinaryModel.load(self.ml_runtime.model_path)
        if tuple(self.ml_model.feature_names) != tuple(FEATURE_NAMES):
            raise RuntimeError(
                "ML1 model feature schema does not match runtime code; retrain the model with this branch",
            )
        if self.ml_runtime.mode == "select":
            self.ml_model.assert_selectable(
                allow_shadow_model=self.ml_runtime.allow_shadow_model,
            )
        self.ml_feature_book = CausalFeatureBook()
        self.ml_counts: dict[str, int] = {}

    def _minc(self, key: str) -> None:
        self.ml_counts[key] = self.ml_counts.get(key, 0) + 1

    def _observe_common_factor(self) -> None:
        # Deliberately call the market-factor grandparent.  The factor remains a
        # causal feature, but is not propagated into the candidate engines as a
        # hard veto.  That separation is the reason ML1 can learn the formerly
        # suppressed counterfactual cases.
        EasyChartRE1MarketFactorStrategy._observe_common_factor(self)

    def _observe_feature_bucket(self) -> None:
        items: list[tuple[str, int, Any]] = []
        for instrument_id, timeframe, bar in self.bar_bucket:
            symbol = self.factor_symbols.get(instrument_id)
            if symbol is None:
                instrument = self.instruments[instrument_id]
                symbol = instrument.raw_symbol.value
            items.append((symbol, timeframe, self._candle(bar)))
        self.ml_feature_book.observe_bucket(items)

    def _macro_side(self, instrument_id: InstrumentId) -> Any:
        return getattr(self.scenario_engines[instrument_id], "_macro_side", None)

    def _flow_observation(self, instrument_id: InstrumentId) -> Any:
        analyzer = self.factor_analyzers.get(instrument_id)
        return None if analyzer is None else analyzer.last_observation

    @staticmethod
    def _is_continuation(plan: V5TradePlan) -> bool:
        text = "|".join(
            (
                str(plan.family).upper(),
                str(plan.scenario_path).upper(),
                str(plan.scale_name).upper(),
            ),
        )
        return any(
            token in text
            for token in (
                "ACCEPT",
                "CONTINU",
                "PULLBACK",
                "HORIZONTAL",
                "DIAGONAL",
                "BREAKOUT",
            )
        )

    def _baseline_context_allows(self, instrument_id: InstrumentId, plan: V5TradePlan) -> bool:
        """Approximate the RE1-v2 deterministic router for shadow trading."""

        factor = self.factor_state
        if factor is not None and factor.event_time_ns <= plan.observed_time_ns:
            if factor.side is not plan.side:
                return False
        if not self._is_continuation(plan):
            return True
        macro_side = self._macro_side(instrument_id)
        if macro_side is None or macro_side is plan.side:
            return True
        return factor is not None and factor.side is plan.side

    def _economics(self, instrument_id: InstrumentId, plan: V5TradePlan) -> TradeEconomics:
        instrument = self.instruments[instrument_id]
        entry_fee = float(self.config.estimated_entry_fee_rate)
        stop_fee = float(self.config.estimated_stop_fee_rate)
        target_fee = entry_fee
        return estimate_trade_economics(
            side=plan.side,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            tick_size=float(instrument.price_increment),
            entry_fee_rate=entry_fee,
            target_fee_rate=target_fee,
            stop_fee_rate=stop_fee,
            funding_rate=float(getattr(self.config, "estimated_funding_rate", 0.0)),
            entry_slippage_ticks=int(self.config.estimated_entry_slippage_ticks),
            target_slippage_ticks=self.ml_runtime.target_slippage_ticks,
            stop_slippage_ticks=int(self.config.estimated_stop_slippage_ticks),
        )

    def _score_plan(self, instrument_id: InstrumentId, plan: V5TradePlan) -> _ScoredPlan:
        features = build_plan_features(
            plan,
            feature_book=self.ml_feature_book,
            macro_side=self._macro_side(instrument_id),
            factor_state=self.factor_state,
            flow_observation=self._flow_observation(instrument_id),
        )
        economics = self._economics(instrument_id, plan)
        decision = self.ml_model.decide(
            features,
            economics,
            min_probability=self.ml_runtime.min_probability,
            probability_edge=self.ml_runtime.probability_edge,
            min_expected_net_r=self.ml_runtime.min_expected_net_r,
        )
        baseline_eligible = self._baseline_context_allows(instrument_id, plan)
        event_values: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "instrument_id": str(instrument_id),
            "symbol": plan.symbol,
            "family": plan.family,
            "side": plan.side.name,
            "scenario_path": plan.scenario_path,
            "model_id": self.ml_model.model_id,
            "model_status": self.ml_model.status,
            "ml_mode": self.ml_runtime.mode,
            "ml_raw_probability": decision.raw_probability,
            "ml_target_probability": decision.target_probability,
            "ml_tree_probability_std": decision.tree_probability_std,
            "ml_required_probability": decision.required_probability,
            "ml_expected_net_r": decision.expected_net_r,
            "ml_model_accepted": decision.accepted,
            "ml_decision_reason": decision.reason,
            "ml_baseline_eligible": baseline_eligible,
            "ml_win_net_r": economics.win_net_r,
            "ml_loss_net_r": economics.loss_net_r,
            "ml_break_even_probability": economics.break_even_probability,
            "ml_estimated_win_cost_r": economics.estimated_win_cost_r,
            "ml_estimated_loss_cost_r": economics.estimated_loss_cost_r,
        }
        event_values.update({f"mlf_{name}": value for name, value in features.items()})
        self._record("ml_plan", **event_values)
        self._minc("plans_scored")
        self._minc(f"decision_{decision.reason.lower()}")
        return _ScoredPlan(
            instrument_id=instrument_id,
            plan=plan,
            features=features,
            economics=economics,
            decision=decision,
            baseline_eligible=baseline_eligible,
        )

    @staticmethod
    def _causal_rank(item: _ScoredPlan) -> tuple[Any, ...]:
        plan = item.plan
        return (
            plan.interaction_time_ns,
            -plan.higher_timeframe_minutes,
            plan.setup_observed_time_ns,
            plan.symbol,
            plan.plan_id,
        )

    @staticmethod
    def _ml_rank(item: _ScoredPlan) -> tuple[Any, ...]:
        plan = item.plan
        return (
            -item.decision.expected_net_r,
            -item.decision.target_probability,
            item.decision.tree_probability_std,
            plan.interaction_time_ns,
            -plan.higher_timeframe_minutes,
            plan.symbol,
            plan.plan_id,
        )

    def _selection_pool(self, scored: list[_ScoredPlan]) -> list[_ScoredPlan]:
        if self.ml_runtime.mode == "shadow":
            return sorted(
                (item for item in scored if item.baseline_eligible),
                key=self._causal_rank,
            )
        return sorted(
            (
                item
                for item in scored
                if item.decision.accepted
                and item.plan.gross_rr >= max(1.0, float(self.config.min_gross_rr))
            ),
            key=self._ml_rank,
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
                if plan.gross_rr < max(1.0, float(self.config.min_gross_rr)):
                    self._record(
                        "ml_plan_geometry_rejected",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        gross_rr=plan.gross_rr,
                        minimum=max(1.0, float(self.config.min_gross_rr)),
                    )
                    self._minc("plans_below_minimum_gross_rr")
                    continue
                try:
                    scored.append(self._score_plan(instrument_id, plan))
                except (ValueError, ArithmeticError) as exc:
                    self._record(
                        "ml_plan_scoring_error",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        reason=repr(exc),
                    )
                    self._minc("plan_scoring_error")

        ranked = self._selection_pool(scored)
        if scored and not ranked:
            self._minc("bucket_all_candidates_abstained")
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, item in enumerate(ranked, start=1):
                    self._record(
                        "ml_plan_skipped_global_slot",
                        plan_id=item.plan.plan_id,
                        instrument_id=str(item.instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                        ml_expected_net_r=item.decision.expected_net_r,
                        ml_target_probability=item.decision.target_probability,
                    )
            else:
                selected_index: int | None = None
                for index, item in enumerate(ranked):
                    if self._submit_plan(item.instrument_id, item.plan):
                        selected_index = index
                        self._record(
                            "ml_arbitration_selected",
                            plan_id=item.plan.plan_id,
                            instrument_id=str(item.instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                            scored_candidates=len(scored),
                            ml_mode=self.ml_runtime.mode,
                            model_id=self.ml_model.model_id,
                            ml_expected_net_r=item.decision.expected_net_r,
                            ml_target_probability=item.decision.target_probability,
                        )
                        self._minc("plan_submitted")
                        break
                if selected_index is not None:
                    selected = ranked[selected_index]
                    for rank, item in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "ml_plan_skipped_arbitration",
                            plan_id=item.plan.plan_id,
                            instrument_id=str(item.instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected.plan.plan_id,
                            ml_expected_net_r=item.decision.expected_net_r,
                            ml_target_probability=item.decision.target_probability,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None

    @property
    def ml_diagnostics(self) -> dict[str, Any]:
        return {
            "runtime": {
                "mode": self.ml_runtime.mode,
                "model_path": str(self.ml_runtime.model_path),
                "model_id": self.ml_model.model_id,
                "model_status": self.ml_model.status,
                "min_probability": self.ml_runtime.min_probability,
                "probability_edge": self.ml_runtime.probability_edge,
                "min_expected_net_r": self.ml_runtime.min_expected_net_r,
                "target_slippage_ticks": self.ml_runtime.target_slippage_ticks,
            },
            "counts": dict(sorted(self.ml_counts.items())),
            "feature_count": len(FEATURE_NAMES),
        }


StrategyClass = EasyChartML1Strategy
