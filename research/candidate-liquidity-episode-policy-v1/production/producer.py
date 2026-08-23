from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import time
import uuid
from typing import Any

from .binance_public import PublicBinanceClient, PublicClientConfig
from .config import ProductionConfig
from .contracts import EpisodePlan, RuntimeMode
from .event_store import EventStore, StoreError
from .market_repository import MarketRepository
from .model_bundle import ModelBundle, eligible_plans
from .policy_bridge import RestoredPolicyBridge


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _process_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _select_account_plan(plans: list[EpisodePlan]) -> EpisodePlan | None:
    if not plans:
        return None
    latest = max(plan.order_time_ns for plan in plans)
    group = [plan for plan in plans if plan.order_time_ns == latest]
    group.sort(
        key=lambda plan: (
            -(plan.expected_log_growth or 0.0),
            -(plan.probability_edge or 0.0),
            -plan.mechanism_coherence,
            -plan.gross_rr,
            plan.episode_id,
        )
    )
    return group[0]


class DecisionProducer:
    def __init__(self, config: ProductionConfig) -> None:
        self.config = config
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.owner_id = _process_id()
        self.store = EventStore(config.database_path)
        client = PublicBinanceClient(
            PublicClientConfig(
                base_url=config.binance_http_base,
                timeout_seconds=config.request_timeout_seconds,
                retries=config.request_retries,
            )
        )
        self.repository = MarketRepository(self.store, client)
        self.bridge = RestoredPolicyBridge(self.store, self.repository)
        self.bundle = self._load_bundle()
        if config.mode in {RuntimeMode.PAPER, RuntimeMode.TESTNET}:
            if config.require_model_bundle_for_orders and self.bundle is None:
                raise RuntimeError(
                    f"{config.mode.value} mode refuses to create executable decisions without "
                    f"a causal model bundle at {config.model_bundle}"
                )
        self.store.integrity_check()
        self.store.verify_event_chain()

    def _load_bundle(self) -> ModelBundle | None:
        if not self.config.model_bundle.exists():
            return None
        bundle = ModelBundle.load(self.config.model_bundle)
        return bundle

    def _heartbeat(self, *, cycle: int, state: str) -> None:
        payload = {
            "owner_id": self.owner_id,
            "cycle": cycle,
            "state": state,
            "mode": self.config.mode.value,
            "model_bundle_id": self.bundle.metadata.bundle_id if self.bundle else None,
            "time_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.store.append_event("PRODUCER_HEARTBEAT", payload)
        self.store.set_checkpoint("producer_heartbeat", payload)

    def run_cycle(self) -> dict[str, Any]:
        refresh = self.repository.refresh(
            self.config.symbols,
            initial_backfill_days=self.config.initial_backfill_days,
        )
        interval_ms = self.config.decision_interval_minutes * 60_000
        decision_bucket = refresh.end_time_ms // interval_ms
        checkpoint = self.store.get_checkpoint("last_policy_bucket") or {}
        prior_bucket = int(checkpoint.get("bucket", -1))
        continuity = {
            symbol: {
                stream: self.repository.verify_continuity(
                    symbol,
                    stream=stream,
                    end_time_ms=refresh.end_time_ms,
                    days=min(self.config.rolling_window_days, self.config.initial_backfill_days),
                )
                for stream in ("futures", "mark", "index")
            }
            for symbol in self.config.symbols
        }
        if decision_bucket <= prior_bucket:
            return {
                "evaluated": False,
                "reason": "same_completed_decision_bucket",
                "decision_bucket": decision_bucket,
                "refresh": refresh.to_dict(),
                "continuity": continuity,
            }
        evaluation = self.bridge.evaluate(
            self.config.symbols,
            end_time_ms=refresh.end_time_ms,
            rolling_window_days=self.config.rolling_window_days,
            decision_age_minutes=max(2 * self.config.decision_interval_minutes, 10),
        )
        raw_plans = list(evaluation.plans)
        scored = self.bundle.score(raw_plans, risk_fraction=self.config.risk_fraction) if self.bundle else raw_plans
        eligible = eligible_plans(scored) if self.bundle else []
        observed = 0
        ready = 0
        selected: EpisodePlan | None = None
        if self.config.mode is RuntimeMode.SHADOW:
            for plan in scored:
                observed += int(self.store.enqueue_plan(plan, ready_for_execution=False))
        else:
            selected = _select_account_plan(eligible)
            if selected is not None and self.store.account_slot().get("state") == "FREE":
                ready = int(self.store.enqueue_plan(selected, ready_for_execution=True))
            for plan in scored:
                if selected is None or plan.decision_id != selected.decision_id:
                    self.store.append_event(
                        "DECISION_NOT_SELECTED",
                        {
                            "decision_id": plan.decision_id,
                            "episode_id": plan.episode_id,
                            "symbol": plan.symbol,
                            "reason": (
                                "NOT_POSITIVE_EXPECTED_LOG_GROWTH"
                                if plan not in eligible
                                else "GLOBAL_ACCOUNT_ARBITRATION"
                            ),
                            "expected_log_growth": plan.expected_log_growth,
                            "probability_edge": plan.probability_edge,
                        },
                        event_id=f"not-selected:{plan.decision_id}",
                    )
        evidence = {
            "evaluated": True,
            "mode": self.config.mode.value,
            "decision_bucket": decision_bucket,
            "decision_time_ns": evaluation.decision_time_ns,
            "refresh": refresh.to_dict(),
            "continuity": continuity,
            "data_evidence": evaluation.data_evidence,
            "policy_counts": evaluation.counts,
            "raw_plans": len(raw_plans),
            "scored_plans": len(scored),
            "eligible_plans": len(eligible),
            "observed_inserted": observed,
            "ready_inserted": ready,
            "selected_plan": selected.to_dict() if selected else None,
            "model_bundle_id": self.bundle.metadata.bundle_id if self.bundle else None,
            "source_policy": "restored-liquidity-episode-policy-v1",
            "source_data_is_closed_only": True,
            "uses_future_labels_in_live_decision": False,
            "account_slot": self.store.account_slot(),
            "time_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.store.set_checkpoint("last_policy_bucket", {"bucket": decision_bucket, "evidence": evidence})
        write_json_atomic(self.config.evidence_dir / "latest_cycle.json", evidence)
        self.store.append_event(
            "POLICY_CYCLE_COMPLETED",
            evidence,
            event_id=f"policy-cycle:{decision_bucket}",
        )
        return evidence

    def run(self, *, duration_seconds: float | None = None, once: bool = False) -> dict[str, Any]:
        lease_name = f"producer:{self.config.state_dir.resolve()}"
        ttl = max(60.0, 4.0 * self.config.poll_seconds)
        if not self.store.acquire_lease(lease_name, self.owner_id, ttl):
            raise RuntimeError(f"another producer owns {lease_name}")
        started = time.monotonic()
        cycle = 0
        last_result: dict[str, Any] = {}
        self.store.append_event(
            "PRODUCER_STARTED",
            {
                "owner_id": self.owner_id,
                "mode": self.config.mode.value,
                "config": self.config.to_dict(),
                "restart_checkpoint": self.store.get_checkpoint("producer_heartbeat"),
            },
            event_id=f"producer-start:{self.owner_id}",
        )
        try:
            while True:
                cycle += 1
                self.store.heartbeat_lease(lease_name, self.owner_id, ttl)
                self._heartbeat(cycle=cycle, state="before_cycle")
                try:
                    last_result = self.run_cycle()
                except BaseException as exc:
                    self.store.append_event(
                        "PRODUCER_CYCLE_FAILED",
                        {"cycle": cycle, "error_type": type(exc).__name__, "error": str(exc)},
                    )
                    raise
                self._heartbeat(cycle=cycle, state="after_cycle")
                if once:
                    break
                if duration_seconds is not None and time.monotonic() - started >= duration_seconds:
                    break
                time.sleep(self.config.poll_seconds)
        finally:
            self.store.append_event(
                "PRODUCER_STOPPED",
                {"owner_id": self.owner_id, "cycle": cycle},
                event_id=f"producer-stop:{self.owner_id}",
            )
            self.store.release_lease(lease_name, self.owner_id)
            status = self.store.status()
            write_json_atomic(self.config.evidence_dir / "runtime_status.json", status)
            self.store.close()
        return last_result
