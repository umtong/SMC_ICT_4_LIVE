#!/usr/bin/env python3
"""Run the canonical latent liquidity-episode policy on Binance Demo Trading.

This is a paper/demo runner, not a live-funds launcher. It uses the canonical
RE1 decision bundle and the same independent reduce-only protective-order
lifecycle used by the backtest runner.

Required environment variables for an actual run:

    BINANCE_DEMO_API_KEY
    BINANCE_DEMO_API_SECRET

Create those keys in Binance Demo Trading API Management. Use Ctrl+C for a
graceful stop. Clear unknown venue positions and open orders before first use;
RE1 never assumes ownership of exposure it did not create.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance import BinanceAccountType
from nautilus_trader.adapters.binance import BinanceDataClientConfig
from nautilus_trader.adapters.binance import BinanceExecClientConfig
from nautilus_trader.adapters.binance import BinanceInstrumentProviderConfig
from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
from nautilus_trader.adapters.binance import BinanceLiveExecClientFactory
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from nautilus_trader.config import CacheConfig
from nautilus_trader.config import LiveDataEngineConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import ClientId, InstrumentId, TraderId

from easychart_re1_bot import EasyChartRE1BotBundle
from execution_re1 import EasyChartMTFConfig
from paper_re1 import build_warmup_map
from paper_re1_bot import EasyChartRE1BotPaperStrategy
import mtf_strategy as _base_strategy
from simple_contract_v14 import FIXED_RISK_FRACTION, MINIMUM_GROSS_RR


DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BINANCE_DEMO_REST = "https://demo-fapi.binance.com"


def _signed_demo_request(
    method: str,
    path: str,
    params: dict[str, str | int],
    api_key: str,
    api_secret: str,
) -> object:
    signed = dict(params)
    signed["timestamp"] = int(time.time() * 1000)
    signed["recvWindow"] = 10_000
    query = urllib.parse.urlencode(signed)
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    request = urllib.request.Request(
        f"{BINANCE_DEMO_REST}{path}?{query}&signature={signature}",
        method=method,
        headers={"X-MBX-APIKEY": api_key},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict) and int(payload.get("code", 0)) < 0:
        raise RuntimeError(f"Binance Demo request rejected: {payload}")
    return payload


def _configure_exchange_leverage(
    symbols: tuple[str, ...],
    api_key: str,
    api_secret: str,
) -> dict[str, int]:
    """Give fixed-risk quantities the venue margin they require.

    The exchange setting is deliberately the largest bracket-supported value;
    actual economic leverage remains quantity times entry divided by NAV.
    """
    payload = _signed_demo_request(
        "GET",
        "/fapi/v1/leverageBracket",
        {},
        api_key,
        api_secret,
    )
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected Binance leverage brackets: {payload!r}")
    brackets_by_symbol = {
        str(item["symbol"]): item["brackets"]
        for item in payload
        if isinstance(item, dict) and "symbol" in item and "brackets" in item
    }
    configured: dict[str, int] = {}
    for symbol in symbols:
        brackets = brackets_by_symbol.get(symbol)
        if not brackets:
            raise RuntimeError(f"missing Binance leverage bracket for {symbol}")
        leverage = max(int(item["initialLeverage"]) for item in brackets)
        response = _signed_demo_request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
            api_key,
            api_secret,
        )
        if not isinstance(response, dict) or int(response.get("leverage", 0)) != leverage:
            raise RuntimeError(f"failed to set Binance leverage for {symbol}: {response!r}")
        configured[symbol] = leverage
    return configured


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--warmup-days", type=int, default=35)
    parser.add_argument("--cache", type=Path, default=Path(".cache/candidate-easychart-re1-paper"))
    parser.add_argument("--entry-fee-rate", type=float, default=0.0005)
    parser.add_argument("--stop-fee-rate", type=float, default=0.0005)
    parser.add_argument("--funding-reserve-rate", type=float, default=0.0001)
    parser.add_argument("--entry-slippage-ticks", type=int, default=2)
    parser.add_argument("--stop-slippage-ticks", type=int, default=2)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate deterministic IDs/configuration without credentials, downloads, or connections",
    )
    return parser.parse_args()


def _instrument_id(symbol: str) -> InstrumentId:
    return InstrumentId.from_str(f"{symbol}-PERP.{BINANCE}")


def _bar_types(instrument_id: InstrumentId) -> tuple[BarType, BarType, BarType, BarType]:
    # Exchange-native closed bars avoid beginning an internal 5m/15m/1h
    # aggregator from a partial interval after warmup replay.
    execution = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
    trigger = BarType.from_str(f"{instrument_id}-5-MINUTE-LAST-EXTERNAL")
    decision = BarType.from_str(f"{instrument_id}-15-MINUTE-LAST-EXTERNAL")
    higher = BarType.from_str(f"{instrument_id}-1-HOUR-LAST-EXTERNAL")
    return execution, trigger, decision, higher


def _validated_inputs(args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[InstrumentId, ...]]:
    symbols = tuple(dict.fromkeys(str(item).upper() for item in args.symbols))
    if set(symbols) != set(DEFAULT_SYMBOLS) or len(symbols) != len(DEFAULT_SYMBOLS):
        raise SystemExit(
            "RE1 is a fixed four-symbol account; --symbols must contain BTCUSDT ETHUSDT SOLUSDT XRPUSDT exactly once",
        )
    if args.warmup_days < 7:
        raise SystemExit("--warmup-days must be at least 7")
    if args.entry_fee_rate < 0 or args.stop_fee_rate < 0 or args.funding_reserve_rate < 0:
        raise SystemExit("fee and funding reserves cannot be negative")
    if args.entry_slippage_ticks < 0 or args.stop_slippage_ticks < 0:
        raise SystemExit("slippage ticks cannot be negative")
    instrument_ids = tuple(_instrument_id(symbol) for symbol in symbols)
    return symbols, instrument_ids


def main() -> None:
    args = parse_args()
    symbols, instrument_ids = _validated_inputs(args)
    bars = [_bar_types(instrument_id) for instrument_id in instrument_ids]
    execution_types = tuple(item[0] for item in bars)
    trigger_types = tuple(item[1] for item in bars)
    decision_types = tuple(item[2] for item in bars)
    higher_types = tuple(item[3] for item in bars)

    check_record = {
        "candidate": "candidate-latent-liquidity-episode-policy-v1",
        "environment": "BINANCE_DEMO_USDT_FUTURES",
        "scenario_bundle": "EasyChartRE1BotBundle",
        "paper_strategy": "EasyChartRE1BotPaperStrategy",
        "symbols": symbols,
        "instrument_ids": [str(item) for item in instrument_ids],
        "execution_bar_types": [str(item) for item in execution_types],
        "trigger_bar_types": [str(item) for item in trigger_types],
        "decision_bar_types": [str(item) for item in decision_types],
        "higher_bar_types": [str(item) for item in higher_types],
        "risk_fraction": float(FIXED_RISK_FRACTION),
        "minimum_gross_rr": float(MINIMUM_GROSS_RR),
        "warmup_days": args.warmup_days,
    }
    if args.check_config:
        print(json.dumps(check_record, ensure_ascii=False, indent=2, sort_keys=True))
        return

    missing = [
        name
        for name in ("BINANCE_DEMO_API_KEY", "BINANCE_DEMO_API_SECRET")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"missing Demo Trading credentials: {', '.join(missing)}")

    api_key = os.environ["BINANCE_DEMO_API_KEY"]
    api_secret = os.environ["BINANCE_DEMO_API_SECRET"]
    configured_leverage = _configure_exchange_leverage(
        symbols,
        api_key,
        api_secret,
    )
    print(
        json.dumps(
            {"configured_exchange_leverage": configured_leverage},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )

    args.cache.mkdir(parents=True, exist_ok=True)
    symbols_by_instrument = dict(zip(instrument_ids, symbols, strict=True))
    warmup = build_warmup_map(symbols_by_instrument, args.warmup_days, args.cache)
    _base_strategy.MultiScaleScenarioBundle = EasyChartRE1BotBundle

    provider = BinanceInstrumentProviderConfig(
        load_ids=frozenset(instrument_ids),
        query_commission_rates=True,
    )
    node_config = TradingNodeConfig(
        trader_id=TraderId("EASYCHART-RE1-DEMO"),
        logging=LoggingConfig(
            log_level=args.log_level,
            log_colors=True,
            use_pyo3=True,
        ),
        data_engine=LiveDataEngineConfig(
            external_clients=[ClientId(BINANCE)],
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            open_check_open_only=False,
            purge_closed_orders_interval_mins=15,
            purge_closed_orders_buffer_mins=60,
            purge_closed_positions_interval_mins=15,
            purge_closed_positions_buffer_mins=60,
            purge_account_events_interval_mins=15,
            purge_account_events_lookback_mins=60,
            purge_from_database=True,
            graceful_shutdown_on_exception=True,
        ),
        cache=CacheConfig(
            timestamps_as_iso8601=True,
            flush_on_start=False,
        ),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=BinanceEnvironment.DEMO,
                instrument_provider=provider,
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=BinanceEnvironment.DEMO,
                instrument_provider=provider,
                max_retries=3,
                log_rejected_due_post_only_as_warning=False,
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=20.0,
        timeout_portfolio=20.0,
        timeout_disconnection=10.0,
        timeout_post_stop=10.0,
    )
    node = TradingNode(config=node_config)
    trading_start_ns = int(datetime.now(UTC).timestamp() * 1_000_000_000)
    strategy = EasyChartRE1BotPaperStrategy(
        EasyChartMTFConfig(
            instrument_ids=instrument_ids,
            higher_bar_types=higher_types,
            decision_bar_types=decision_types,
            trigger_bar_types=trigger_types,
            execution_bar_types=execution_types,
            risk_fraction=float(FIXED_RISK_FRACTION),
            min_gross_rr=float(MINIMUM_GROSS_RR),
            estimated_entry_fee_rate=args.entry_fee_rate,
            estimated_stop_fee_rate=args.stop_fee_rate,
            estimated_funding_rate=args.funding_reserve_rate,
            estimated_entry_slippage_ticks=args.entry_slippage_ticks,
            estimated_stop_slippage_ticks=args.stop_slippage_ticks,
            trading_start_ns=trading_start_ns,
        ),
        warmup=warmup,
    )
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
