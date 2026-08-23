from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from .binance_public import PublicBinanceClient, PublicClientConfig
from .config import ProductionConfig
from .contracts import RuntimeMode
from .event_store import EventStore
from .historical import run_continuous_reproduction
from .model_bundle import train_bundle
from .nautilus_smoke import run_smoke
from .nautilus_execution import run_nautilus_node
from .producer import DecisionProducer, write_json_atomic
from .supervisor import run_supervisor
from .verification import reconcile, verify


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def doctor(config: ProductionConfig) -> dict[str, Any]:
    client = PublicBinanceClient(
        PublicClientConfig(
            base_url=config.binance_http_base,
            timeout_seconds=config.request_timeout_seconds,
            retries=config.request_retries,
        )
    )
    server_time = client.server_time_ms()
    info = client.exchange_info()
    available = {
        item["symbol"]: {
            "status": item.get("status"),
            "contractType": item.get("contractType"),
            "quoteAsset": item.get("quoteAsset"),
        }
        for item in info.get("symbols", [])
        if item.get("symbol") in config.symbols
    }
    missing = sorted(set(config.symbols) - set(available))
    if missing:
        raise RuntimeError(f"symbols missing from Binance USD-M exchangeInfo: {missing}")
    sample = {}
    for symbol in config.symbols:
        rows = client.klines(
            symbol,
            stream="futures",
            start_time_ms=server_time - 10 * 60_000,
            end_time_ms=server_time,
            server_time_ms=server_time,
        )
        if not rows:
            raise RuntimeError(f"no closed public futures bars for {symbol}")
        sample[symbol] = {
            "closed_rows": len(rows),
            "last_open_time_ms": rows[-1]["open_time_ms"],
            "last_close_time_ms": rows[-1]["close_time_ms"],
            "last_close": rows[-1]["close"],
        }
    return {
        "server_time_ms": server_time,
        "symbols": available,
        "closed_public_sample": sample,
        "mode": config.mode.value,
        "public_market_connected": True,
        "order_capability": config.mode is not RuntimeMode.SHADOW,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="episode-policy-production",
        description="Windows/Linux production-candidate runtime for the restored liquidity episode policy",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--config", type=Path)
    verify_parser.add_argument("--output", type=Path)

    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--config", type=Path, required=True)
    doctor_parser.add_argument("--output", type=Path)

    producer_parser = sub.add_parser("producer")
    producer_parser.add_argument("--config", type=Path, required=True)
    producer_parser.add_argument("--duration-seconds", type=float)
    producer_parser.add_argument("--once", action="store_true")

    shadow_parser = sub.add_parser("shadow")
    shadow_parser.add_argument("--config", type=Path, required=True)
    shadow_parser.add_argument("--duration-seconds", type=float)
    shadow_parser.add_argument("--once", action="store_true")

    paper_parser = sub.add_parser("paper")
    paper_parser.add_argument("--config", type=Path, required=True)
    paper_parser.add_argument("--duration-seconds", type=float)

    testnet_parser = sub.add_parser("testnet")
    testnet_parser.add_argument("--config", type=Path, required=True)
    testnet_parser.add_argument("--duration-seconds", type=float)

    node_parser = sub.add_parser("nautilus-node")
    node_parser.add_argument("--config", type=Path, required=True)

    model_parser = sub.add_parser("build-model")
    model_parser.add_argument("--root", type=Path, required=True)
    model_parser.add_argument("--output", type=Path, required=True)
    model_parser.add_argument("--cutoff", required=True)
    model_parser.add_argument("--risk-fraction", type=float, default=0.03)

    historical_parser = sub.add_parser("historical-continuous")
    historical_parser.add_argument("--start", type=date.fromisoformat, required=True)
    historical_parser.add_argument("--end", type=date.fromisoformat, required=True)
    historical_parser.add_argument("--development-end", type=date.fromisoformat, required=True)
    historical_parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    historical_parser.add_argument("--warmup-days", type=int, default=75)
    historical_parser.add_argument("--cache", type=Path, required=True)
    historical_parser.add_argument("--output", type=Path, required=True)

    smoke_parser = sub.add_parser("nautilus-smoke")
    smoke_parser.add_argument("--output", type=Path, required=True)

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--config", type=Path, required=True)

    reconcile_parser = sub.add_parser("reconcile")
    reconcile_parser.add_argument("--database", type=Path, required=True)
    reconcile_parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "verify":
        payload = verify(args.config)
        if args.output:
            write_json_atomic(args.output, payload)
        _print(payload)
        return
    if args.command == "doctor":
        config = ProductionConfig.load(args.config)
        payload = doctor(config)
        if args.output:
            write_json_atomic(args.output, payload)
        _print(payload)
        return
    if args.command in {"producer", "shadow"}:
        config = ProductionConfig.load(args.config)
        if args.command == "shadow" and config.mode is not RuntimeMode.SHADOW:
            raise SystemExit("shadow command requires mode=shadow")
        payload = DecisionProducer(config).run(
            duration_seconds=args.duration_seconds,
            once=args.once,
        )
        _print(payload)
        return
    if args.command in {"paper", "testnet"}:
        config = ProductionConfig.load(args.config)
        required = RuntimeMode.PAPER if args.command == "paper" else RuntimeMode.TESTNET
        if config.mode is not required:
            raise SystemExit(f"{args.command} command requires mode={required.value}")
        raise SystemExit(run_supervisor(args.config, duration_seconds=args.duration_seconds))
    if args.command == "nautilus-node":
        config = ProductionConfig.load(args.config)
        asyncio.run(run_nautilus_node(config))
        return
    if args.command == "build-model":
        bundle = train_bundle(
            args.root,
            args.output,
            cutoff=args.cutoff,
            risk_fraction=args.risk_fraction,
        )
        _print(bundle.metadata.to_dict())
        return
    if args.command == "historical-continuous":
        payload = run_continuous_reproduction(
            start=args.start,
            end=args.end,
            development_end=args.development_end,
            symbols=tuple(args.symbols),
            warmup_days=args.warmup_days,
            cache=args.cache,
            output=args.output,
        )
        _print(payload)
        return
    if args.command == "nautilus-smoke":
        _print(run_smoke(args.output))
        return
    if args.command == "status":
        config = ProductionConfig.load(args.config)
        with EventStore(config.database_path) as store:
            _print(store.status())
        return
    if args.command == "reconcile":
        payload = reconcile(args.database)
        if args.output:
            write_json_atomic(args.output, payload)
        _print(payload)
        return
    raise AssertionError(args.command)


if __name__ == "__main__":
    main()
