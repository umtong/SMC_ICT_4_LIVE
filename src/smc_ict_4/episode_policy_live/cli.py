"""Command line interface for the integrated paper/shadow production candidate."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import platform
import sys
import time

from .domain import DEFAULT_CONTRACTS, SYMBOLS
from .live import (
    NT,
    bootstrap_store,
    build_node,
    native_restart_block_reason,
    native_restart_capabilities,
    run_node_blocking,
)
from .live_bars import DEFAULT_WARMUP_MINUTES
from .replay_runner import run_native_replay
from .storage import StateStore


def command_verify(args: argparse.Namespace) -> int:
    result: dict[str, object] = {
        "python": sys.version,
        "platform": platform.platform(),
        "symbols": list(SYMBOLS),
        "risk_fraction": 0.03,
        "contracts": {
            symbol: {
                "tick_size": str(contract.tick_size),
                "quantity_step": str(contract.quantity_step),
                "min_quantity": str(contract.min_quantity),
                "min_notional": str(contract.min_notional),
                "max_leverage": str(contract.max_leverage),
            }
            for symbol, contract in DEFAULT_CONTRACTS.items()
        },
        "nautilus_available": NT is not None,
        "native_restart_capabilities": native_restart_capabilities(),
    }
    if NT is not None:
        import nautilus_trader

        result["nautilus_version"] = nautilus_trader.__version__
        if args.build_node:
            node = build_node(
                execution_mode="SHADOW",
                state_path=args.state or Path("artifacts/episode-policy-verify/node-state.sqlite"),
                initial_nav=100_000.0,
            )
            node.dispose()
            result["nautilus_node_build"] = "ok"
    elif args.build_node:
        raise RuntimeError("--build-node requires nautilus_trader")
    state_path = args.state or Path("artifacts/episode-policy-verify/state.sqlite")
    with StateStore(state_path) as store:
        store.append_event(time_ns=time.time_ns(), event_type="VERIFY", payload={"ok": True})
        store.save_snapshot("verify", time_ns=time.time_ns(), payload=result)
        result["sqlite_integrity"] = store.integrity_check()
        result["event_hash_chain_valid"] = store.verify_hash_chain()
        result["sqlite_counts"] = store.counts()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def command_replay(args: argparse.Namespace) -> int:
    summary = run_native_replay(
        monthly_roots=args.monthly_root,
        start=args.start,
        end=args.end,
        output=args.output,
        warmup_days=args.warmup_days,
        initial_nav=args.initial_nav,
        metrics_root=args.metrics_root,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def command_bootstrap(args: argparse.Namespace) -> int:
    with StateStore(args.state) as store:
        counts = bootstrap_store(store, limit=args.limit)
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


def command_runtime(args: argparse.Namespace) -> int:
    mode = args.mode.upper()
    if mode == "TESTNET" and not args.confirm_testnet:
        raise SystemExit("TESTNET execution requires --confirm-testnet")
    run_node_blocking(
        execution_mode=mode,
        state_path=args.state,
        duration_seconds=args.duration_seconds,
        initial_nav=args.initial_nav,
        bootstrap=not args.no_bootstrap,
        bootstrap_lookback_minutes=args.bootstrap_minutes,
        live_inventory_poll_seconds=args.inventory_poll_seconds,
    )
    return 0


def command_status(args: argparse.Namespace) -> int:
    with StateStore(args.state) as store:
        runtime = store.load_snapshot("strategy_runtime")
        result = {
            "state_path": str(args.state.resolve()),
            "integrity": store.integrity_check(),
            "hash_chain_valid": store.verify_hash_chain(),
            "counts": store.counts(),
            "shadow_account": store.load_snapshot("shadow_account"),
            "strategy_runtime": runtime,
            "native_restart_capabilities": native_restart_capabilities(),
            "native_restart_block_reason": (
                native_restart_block_reason(args.mode.upper(), runtime)
                if args.mode is not None
                else None
            ),
        }
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def command_backup(args: argparse.Namespace) -> int:
    with StateStore(args.state) as store:
        store.backup(args.output)
        result = {
            "source": str(args.state.resolve()),
            "backup": str(args.output.resolve()),
            "integrity": store.integrity_check(),
            "hash_chain_valid": store.verify_hash_chain(),
            "counts": store.counts(),
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify", help="verify imports, contracts, and durable state")
    verify.add_argument("--state", type=Path)
    verify.add_argument("--output", type=Path)
    verify.add_argument("--build-node", action="store_true")
    verify.set_defaults(handler=command_verify)

    replay = commands.add_parser(
        "replay",
        help="run official monthly Binance data through one native Nautilus account",
    )
    replay.add_argument("--start", type=date.fromisoformat, required=True)
    replay.add_argument("--end", type=date.fromisoformat, required=True)
    replay.add_argument(
        "--monthly-root",
        type=Path,
        action="append",
        required=True,
        help="repeatable Binance Vision futures_um/monthly directory",
    )
    replay.add_argument("--output", type=Path, required=True)
    replay.add_argument("--warmup-days", type=int, default=90)
    replay.add_argument("--initial-nav", type=float, default=100_000.0)
    replay.add_argument(
        "--metrics-root",
        type=Path,
        help="optional Binance Vision futures_um/daily/metrics directory",
    )
    replay.set_defaults(handler=command_replay)

    bootstrap = commands.add_parser("bootstrap", help="download recent public 1m bars into state")
    bootstrap.add_argument("--state", type=Path, required=True)
    bootstrap.add_argument("--limit", type=int, default=DEFAULT_WARMUP_MINUTES)
    bootstrap.set_defaults(handler=command_bootstrap)

    runtime = commands.add_parser("run", help="run live public-data shadow, sandbox paper, or testnet")
    runtime.add_argument("--mode", choices=["shadow", "sandbox", "testnet"], required=True)
    runtime.add_argument("--state", type=Path, required=True)
    runtime.add_argument("--duration-seconds", type=int)
    runtime.add_argument("--initial-nav", type=float, default=100_000.0)
    runtime.add_argument(
        "--bootstrap-minutes",
        type=int,
        default=DEFAULT_WARMUP_MINUTES,
        help="completed public 1m bars per symbol (default: 10080, seven days)",
    )
    runtime.add_argument(
        "--inventory-poll-seconds",
        type=float,
        default=15.0,
        help="public OI/global-ratio polling interval (network runs off node loop)",
    )
    runtime.add_argument("--no-bootstrap", action="store_true")
    runtime.add_argument("--confirm-testnet", action="store_true")
    runtime.set_defaults(handler=command_runtime)

    status = commands.add_parser("status", help="inspect durable runtime state")
    status.add_argument("--state", type=Path, required=True)
    status.add_argument("--mode", choices=["shadow", "sandbox", "testnet"])
    status.set_defaults(handler=command_status)

    backup = commands.add_parser("backup", help="atomically back up the runtime database")
    backup.add_argument("--state", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    backup.set_defaults(handler=command_backup)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
