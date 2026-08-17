"""NautilusTrader binding for the EasyChart ML2 causal meta-policy.

NautilusTrader and the inherited RE1 execution layer remain the authority for
one continuous account, one global position slot, market entry, fixed protective
orders, fees, fills and approximately 3% NAV loss at the structural stop.  ML2
only observes complete immutable plans, estimates target-before-stop
probability and selects the highest positive expected log-growth opportunity.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from contracts_v5 import V5TradePlan
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy
from execution_re1_market_factor import EasyChartRE1MarketFactorStrategy
from nautilus_trader.model.identifiers import InstrumentId

from ml2_context import (
    FactorTransitionBook,
    factor_side_name,
    inherited_preplan_factor_allows,
    plan_factor_snapshots,
)
from ml2_features import CausalFeatureBook, FEATURE_NAMES, build_plan_features
from ml2_model import (
    CatBoostProbabilityModel,
    ML2Decision,
    TradeEconomics,
    estimate_trade_economics,
    shadow_decision,
)


@dataclass(frozen=True, slots=True)
class ML2RuntimeConfig:
    mode: str = "shadow"
    model_metadata: Path = Path(__file__).resolve().parent / "models" / "untrained.json"

    def __post_init__(self) -> None:
        if self.mode not in {"shadow", "select"}:
            raise ValueError("ML2 mode must be 'shadow' or 'select'")

    @property
    def model_path(self) -> Path:
        return self.model_metadata


_RUNTIME = ML2RuntimeConfig()


def configure_ml2_runtime(config: ML2RuntimeConfig) -> None:
    global _RUNTIME
    _RUNTIME = config


@dataclass(frozen=True, slots=True)
class _ScoredPlan:
    instrument_id: InstrumentId
    plan: V5TradePlan
    causal_family: str
    features: dict[str, float]
    economics: TradeEconomics
    decision: ML2Decision
    baseline_eligible: bool


class EasyChartML2Strategy(EasyChartRE1LocalAuctionStrategy):
    """Calibrated selector over complete EasyChart plans."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.ml_runtime = _RUNTIME
        self.ml_feature_book = CausalFeatureBook()
        self.ml_factor_book = FactorTransitionBook()
        self.ml_counts: dict[str, int] = {}
        self.ml_model: CatBoostProbabilityModel | None = None
        if self.ml_runtime.mode == "select":
            model = CatBoostProbabilityModel(self.ml_runtime.model_metadata)
            model.assert_selectable()
            if tuple(model.feature_names) != tuple(FEATURE_NAMES):
                raise RuntimeError(
                    "ML2 model feature schema does not match runtime code; rebuild dataset and model",
                )
            configured_risk = float(self.config.risk_fraction)
            if not math.isclose(
                configured_risk,
                model.risk_fraction,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise RuntimeError(
                    f"ML2 model was trained for risk_fraction={model.risk_fraction}, "
                    f"but runtime uses {configured_risk}",
                )
            self.ml_model = model

    @property
    def _model_id(self) -> str:
        return "shadow-only" if self.ml_model is None else self.ml_model.model_id

    @property
    def _model_status(self) -> str:
        return "shadow_only" if self.ml_model is None else self.ml_model.status

    def _minc(self, key: str) -> None:
        self.ml_counts[key] = self.ml_counts.get(key, 0) + 1

    def _observe_common_factor(self) -> None:
        # Observe the true synchronized factor first.  ML2 candidate bundles
        # then receive it through an observation-only setter which neutralizes
        # only context vetoes, not factor-created scenario state.
        EasyChartRE1MarketFactorStrategy._observe_common_factor(self)
        self.ml_factor_book.observe(
            int(self.bar_bucket_ts or 0),
            getattr(self, "factor_state", None),
        )
        for bundle in self.scenario_engines.values():
            setter = getattr(bundle, "set_market_factor_state", None)
            if setter is not None:
                setter(getattr(self, "factor_state", None))

    def _observe_feature_bucket(self) -> None:
        items: list[tuple[str, int, Any]] = []
        for instrument_id, timeframe, bar in self.bar_bucket:
            symbol = self.factor_symbols.get(instrument_id)
            if symbol is None:
                symbol = self.instruments[instrument_id].raw_symbol.value
            items.append((symbol, timeframe, self._candle(bar)))
        self.ml_feature_book.observe_bucket(items)

    def _macro_side_for(self, instrument_id: InstrumentId) -> Any:
        return getattr(self.scenario_engines[instrument_id], "_macro_side", None)

    def _flow_observation(self, instrument_id: InstrumentId) -> Any:
        analyzer = self.factor_analyzers.get(instrument_id)
        return None if analyzer is None else analyzer.last_observation

    @staticmethod
    def _is_continuation(plan: V5TradePlan) -> bool:
        """Match inherited continuation ownership without name collisions."""

        scenario = str(plan.scenario_path).upper()
        if any(token in scenario for token in ("REJECT", "ROTATION", "BOUNCE", "RECLAIM")):
            return False
        text = "|".join(
            (
                str(plan.family).upper(),
                scenario,
                str(plan.scale_name).upper(),
            ),
        )
        return any(
            token in text
            for token in (
                "ACCEPT",
                "CONTINU",
                "PULLBACK",
                "HORIZONTAL_FLIP",
                "MATURE_DIAGONAL",
                "BREAKOUT",
            )
        )

    def _baseline_context_allows(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> bool:
        """Reconstruct the inherited deterministic policy for shadow execution."""

        if not inherited_preplan_factor_allows(plan, self.ml_factor_book):
            return False
        factor = getattr(self, "factor_state", None)
        if factor is not None and factor.event_time_ns <= plan.observed_time_ns:
            if factor.side is not plan.side:
                return False
        if not self._is_continuation(plan):
            return True
        macro_side = self._macro_side_for(instrument_id)
        if macro_side is None or macro_side is plan.side:
            return True
        return factor is not None and factor.side is plan.side

    def _economics(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> TradeEconomics:
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

    def _score_plan(
        self,
        instrument_id: InstrumentId,
        plan: V5TradePlan,
    ) -> _ScoredPlan:
        engine = self.scenario_engines[instrument_id]
        setup_factor, pre_response_factor = plan_factor_snapshots(
            plan,
            self.ml_factor_book,
        )
        causal_family, features = build_plan_features(
            plan,
            feature_book=self.ml_feature_book,
            macro_side=self._macro_side_for(instrument_id),
            factor_state=getattr(self, "factor_state", None),
            setup_factor_state=setup_factor,
            pre_response_factor_state=pre_response_factor,
            flow_observation=self._flow_observation(instrument_id),
            zone_lookup=engine.find_zone,
        )
        economics = self._economics(instrument_id, plan)
        decision = (
            shadow_decision(economics, risk_fraction=float(self.config.risk_fraction))
            if self.ml_model is None
            else self.ml_model.decide(features, economics)
        )
        baseline_eligible = self._baseline_context_allows(instrument_id, plan)
        event_values: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "causal_event_id": plan.causal_event_id,
            "instrument_id": str(instrument_id),
            "symbol": plan.symbol,
            "family": plan.family,
            "side": plan.side.name,
            "scenario_path": plan.scenario_path,
            "ml2_causal_family": causal_family,
            "model_id": self._model_id,
            "model_status": self._model_status,
            "ml_mode": self.ml_runtime.mode,
            "ml2_raw_probability": decision.raw_probability,
            "ml2_target_probability": decision.target_probability,
            "ml2_required_log_probability": decision.required_probability,
            "ml2_arithmetic_break_even_probability": decision.arithmetic_break_even_probability,
            "ml2_expected_net_r": decision.expected_net_r,
            "ml2_expected_log_growth": decision.expected_log_growth,
            "ml2_model_accepted": decision.accepted,
            "ml2_decision_reason": decision.reason,
            "ml2_baseline_eligible": baseline_eligible,
            "ml2_setup_factor_side": factor_side_name(setup_factor),
            "ml2_pre_response_factor_side": factor_side_name(pre_response_factor),
            "ml2_win_net_r": economics.win_net_r,
            "ml2_loss_net_r": economics.loss_net_r,
            "ml2_estimated_win_cost_r": economics.estimated_win_cost_r,
            "ml2_estimated_loss_cost_r": economics.estimated_loss_cost_r,
        }
        event_values.update({f"ml2f_{name}": value for name, value in features.items()})
        self._record("ml2_plan", **event_values)
        self._minc("plans_scored")
        self._minc(f"family_{causal_family.lower()}")
        self._minc(f"decision_{decision.reason.lower()}")
        self._minc("baseline_eligible" if baseline_eligible else "baseline_ineligible")
        return _ScoredPlan(
            instrument_id=instrument_id,
            plan=plan,
            causal_family=causal_family,
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
            -item.decision.expected_log_growth,
            -item.decision.expected_net_r,
            -item.decision.target_probability,
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
                minimum_rr = max(1.0, float(self.config.min_gross_rr))
                if plan.gross_rr < minimum_rr:
                    self._record(
                        "ml2_plan_geometry_rejected",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        gross_rr=plan.gross_rr,
                        minimum=minimum_rr,
                    )
                    self._minc("plans_below_minimum_gross_rr")
                    continue
                # Feature/model failures are implementation errors.  Do not
                # hide them behind a permissive or conservative fallback.
                scored.append(self._score_plan(instrument_id, plan))

        ranked = self._selection_pool(scored)
        if scored and not ranked:
            self._minc("bucket_no_selectable_candidate")
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, item in enumerate(ranked, start=1):
                    self._record(
                        "ml2_plan_skipped_global_slot",
                        plan_id=item.plan.plan_id,
                        instrument_id=str(item.instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                        ml2_expected_log_growth=item.decision.expected_log_growth,
                        ml2_expected_net_r=item.decision.expected_net_r,
                        ml2_target_probability=item.decision.target_probability,
                    )
            else:
                selected_index: int | None = None
                for index, item in enumerate(ranked):
                    if self._submit_plan(item.instrument_id, item.plan):
                        selected_index = index
                        self._record(
                            "ml2_arbitration_selected",
                            plan_id=item.plan.plan_id,
                            instrument_id=str(item.instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                            scored_candidates=len(scored),
                            ml_mode=self.ml_runtime.mode,
                            model_id=self._model_id,
                            causal_family=item.causal_family,
                            ml2_expected_log_growth=item.decision.expected_log_growth,
                            ml2_expected_net_r=item.decision.expected_net_r,
                            ml2_target_probability=item.decision.target_probability,
                        )
                        self._minc("plan_submitted")
                        break
                if selected_index is not None:
                    selected = ranked[selected_index]
                    for rank, item in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "ml2_plan_skipped_arbitration",
                            plan_id=item.plan.plan_id,
                            instrument_id=str(item.instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected.plan.plan_id,
                            ml2_expected_log_growth=item.decision.expected_log_growth,
                            ml2_expected_net_r=item.decision.expected_net_r,
                            ml2_target_probability=item.decision.target_probability,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None

    @property
    def ml_diagnostics(self) -> dict[str, Any]:
        return {
            "runtime": {
                "mode": self.ml_runtime.mode,
                "model_metadata": str(self.ml_runtime.model_metadata),
                "model_id": self._model_id,
                "model_status": self._model_status,
                "decision": "positive_expected_log_nav_growth_at_fixed_risk",
                "simultaneous_arbitration": "highest_expected_log_growth",
            },
            "counts": dict(sorted(self.ml_counts.items())),
            "feature_count": len(FEATURE_NAMES),
            "factor_transitions": self.ml_factor_book.transitions,
            "risk_fraction_is_not_model_controlled": True,
        }


StrategyClass = EasyChartML2Strategy
