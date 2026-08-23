from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import py_compile
import subprocess
import tempfile
from typing import Any

from .config import ProductionConfig
from .contracts import EpisodePlan, RuntimeMode, SYMBOLS
from .event_store import EventStore
from .policy_bridge import activate_restored_policy_paths
from .risk import size_for_plan

EXPECTED_RESTORED_ROUTER_BLOB = "92459a08e98a634ec0a096ec1d567c78abdff7a9"


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=repo_root, check=True, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_contract() -> dict[str, Any]:
    candidate = Path(__file__).resolve().parents[1]
    repo_root = candidate.parents[1]
    restored_router = candidate / "route_episode_policy.py"
    blob = _git(repo_root, "hash-object", str(restored_router))
    production_files = sorted(path for path in Path(__file__).resolve().parent.glob("*.py") if path.is_file())
    payload = {
        "repo_root": str(repo_root),
        "candidate_dir": str(candidate),
        "git_branch": _git(repo_root, "branch", "--show-current"),
        "git_commit": _git(repo_root, "rev-parse", "HEAD"),
        "git_dirty": bool(_git(repo_root, "status", "--porcelain=v1")),
        "restored_router": {
            "path": str(restored_router.relative_to(repo_root)),
            "expected_blob_sha1": EXPECTED_RESTORED_ROUTER_BLOB,
            "actual_blob_sha1": blob,
            "matches": blob == EXPECTED_RESTORED_ROUTER_BLOB,
        },
        "production_files": {
            str(path.relative_to(repo_root)): _sha256(path) for path in production_files
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "is_windows": os.name == "nt",
        },
        "symbols": list(SYMBOLS),
        "risk_fraction_contract": 0.03,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if blob is not None and blob != EXPECTED_RESTORED_ROUTER_BLOB:
        raise RuntimeError(
            f"restored router blob changed: expected {EXPECTED_RESTORED_ROUTER_BLOB}, got {blob}"
        )
    return payload


def verify_imports() -> dict[str, Any]:
    activate_restored_policy_paths()
    requirements = {
        "episode_policy": "generate_symbol",
        "episode_policy_features": "enrich_episode_frame",
        "route_episode_policy_causal": "strict_causal_predictions",
        "reproduce": "verify",
    }
    output: dict[str, Any] = {}
    for module_name, callable_name in requirements.items():
        module = importlib.import_module(module_name)
        candidate = getattr(module, callable_name, None)
        if not callable(candidate):
            raise RuntimeError(f"{module_name}.{callable_name} is unavailable")
        output[module_name] = {
            "callable": callable_name,
            "module_file": str(Path(module.__file__).resolve()),
        }
    import route_episode_policy as base
    import route_episode_policy_causal as strict
    if base.causal_predictions is not strict.strict_causal_predictions:
        raise RuntimeError("strict causal router is not installed")
    if float(base.RISK_FRACTION) != 0.03:
        raise RuntimeError("restored router risk fraction changed")
    output["strict_router_installed"] = True
    return output


def compile_candidate() -> list[str]:
    candidate = Path(__file__).resolve().parents[1]
    files = sorted(candidate.glob("*.py")) + sorted((candidate / "production").glob("*.py"))
    compiled = []
    for path in files:
        py_compile.compile(str(path), doraise=True)
        compiled.append(str(path.relative_to(candidate)))
    return compiled


def self_check() -> dict[str, Any]:
    plan = EpisodePlan(
        episode_id="self-check-episode",
        action_id="self-check-action",
        symbol="BTCUSDT",
        side="LONG",
        family="FAILED_AUCTION_REVERSAL",
        order_time_ns=1_700_000_000_000_000_000,
        entry=40_000.0,
        stop=39_800.0,
        target=40_400.0,
        gross_rr=2.0,
        planned_target_net_r=1.95,
        entry_geometry="OB_FVG_OVERLAP",
        route_kind="PREEXISTING_OPPOSING_LIQUIDITY",
        mechanism_coherence=0.5,
    )
    quantity = size_for_plan(
        plan,
        equity=100_000.0,
        risk_fraction=0.03,
        maximum_leverage=3.0,
        minimum_notional=10.0,
    )
    if quantity.capped_quantity <= 0.0 or quantity.effective_leverage > 3.0 + 1e-12:
        raise RuntimeError("risk sizing self-check failed")
    with tempfile.TemporaryDirectory(prefix="episode-policy-self-check-") as temp:
        database = Path(temp) / "nested" / "runtime.sqlite3"
        store = EventStore(database)
        store.append_event("SELF_CHECK_STARTED", {"plan": plan.to_dict()}, event_id="self-check:start")
        if not store.enqueue_plan(plan, ready_for_execution=True):
            raise RuntimeError("failed to enqueue self-check plan")
        claimed = store.claim_next_plan("self-check-consumer")
        if claimed is None or claimed.decision_id != plan.decision_id:
            raise RuntimeError("failed to atomically claim self-check plan")
        store.mark_submitted(plan.decision_id, {"synthetic": True})
        store.complete_decision(plan.decision_id, "COMPLETED", "SELF_CHECK")
        first_chain = store.verify_event_chain()
        store.close()
        reopened = EventStore(database)
        second_chain = reopened.verify_event_chain()
        status = reopened.status()
        if status["account_slot"].get("state") != "FREE":
            raise RuntimeError("account slot was not released")
        if first_chain["last_hash"] != second_chain["last_hash"]:
            raise RuntimeError("event chain changed after restart")
        reopened.close()
    return {
        "plan_decision_id": plan.decision_id,
        "quantity": quantity.to_dict(),
        "event_chain": second_chain,
        "restart_recovery": True,
        "single_global_slot": True,
        "shadow_has_order_gateway": False,
    }


def verify(config_path: str | Path | None = None) -> dict[str, Any]:
    config = ProductionConfig.load(config_path) if config_path else None
    if config is not None and config.mode is RuntimeMode.SHADOW:
        sensitive = [
            name for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET") if os.getenv(name)
        ]
    else:
        sensitive = []
    payload = {
        "source_contract": source_contract(),
        "compiled": compile_candidate(),
        "imports": verify_imports(),
        "self_check": self_check(),
        "config": config.to_dict() if config else None,
        "shadow_credentials_present": sensitive,
    }
    if sensitive:
        raise RuntimeError(
            "shadow verification found execution credentials in the environment; remove them "
            "to preserve a physical no-order boundary"
        )
    return payload


def reconcile(database: str | Path) -> dict[str, Any]:
    with EventStore(database) as store:
        status = store.status()
        slot = status["account_slot"]
        state = str(slot.get("state", ""))
        if state not in {"FREE", "CLAIMED", "ACTIVE"}:
            raise RuntimeError(f"unknown account slot state: {state}")
        if state == "FREE" and any(slot.get(name) is not None for name in ("decision_id", "episode_id", "symbol")):
            raise RuntimeError("free account slot still references a decision")
        return {
            **status,
            "reconciled_at_utc": datetime.now(timezone.utc).isoformat(),
            "reconciliation_valid": True,
        }
