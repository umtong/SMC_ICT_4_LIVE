"""Run the candidate through one NautilusTrader continuous account."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.persistence.wranglers import BarDataWrangler

from data import load_range, wrangler_frame
from instruments import CONTRACTS, make_instrument
from strategy import EasyChartV2Config, EasyChartV2Strategy


STARTING_NAV = 100_000.0
VENUE = Venue("BINANCE")
USDT = Currency.from_str("USDT")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=7)
    parser.add_argument("--symbols", nargs="+", default=list(CONTRACTS))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-prominence-atr", type=float, default=1.0)
    parser.add_argument("--rejection-only", action="store_true")
    parser.add_argument("--acceptance-only", action="store_true")
    return parser.parse_args()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def final_nav(engine: BacktestEngine) -> float:
    account = engine.portfolio.account(VENUE)
    if account is None:
        raise RuntimeError("account unavailable after backtest")
    money = account.balance_total(USDT)
    if money is None:
        raise RuntimeError("USDT balance unavailable after backtest")
    return float(money.as_double())


def build_bars(
    symbol: str,
    instrument: Any,
    start: date,
    end: date,
    cache: Path,
) -> tuple[list[Any], BarType, BarType]:
    raw = load_range(symbol, start, end, cache)
    one = wrangler_frame(raw, 1)
    one_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    # NautilusTrader derives the 5m signal stream from the 1m external bars.
    # Only the 1m source bars move the simulated exchange.
    five_type = BarType.from_str(
        f"{instrument.id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL",
    )
    one_bars = BarDataWrangler(one_type, instrument).process(one)
    return one_bars, one_type, five_type


def main() -> None:
    args = parse_args()
    if args.end < args.start:
        raise SystemExit("--end must be >= --start")
    if args.rejection_only and args.acceptance_only:
        raise SystemExit("choose at most one of --rejection-only/--acceptance-only")
    symbols = tuple(args.symbols)
    unknown = sorted(set(symbols) - set(CONTRACTS))
    if unknown:
        raise SystemExit(f"unknown symbols: {unknown}")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("EASYCHART-V2-001"),
            logging=LoggingConfig(log_level="ERROR"),
            risk_engine=RiskEngineConfig(bypass=False),
        ),
    )
    fill_model = FillModel(
        prob_fill_on_limit=1.0,
        prob_slippage=1.0,
        random_seed=42,
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(STARTING_NAV, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("100"),
        fill_model=fill_model,
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
    )

    instruments = [make_instrument(symbol) for symbol in symbols]
    for instrument in instruments:
        engine.add_instrument(instrument)

    load_start = args.start - timedelta(days=args.warmup_days)
    execution_types: list[BarType] = []
    signal_types: list[BarType] = []
    for symbol, instrument in zip(symbols, instruments, strict=True):
        one_bars, execution_type, signal_type = build_bars(
            symbol,
            instrument,
            load_start,
            args.end,
            args.cache,
        )
        engine.add_data(one_bars, sort=False)
        execution_types.append(execution_type)
        signal_types.append(signal_type)
    engine.sort_data()

    strategy = EasyChartV2Strategy(
        EasyChartV2Config(
            instrument_ids=tuple(instrument.id for instrument in instruments),
            signal_bar_types=tuple(signal_types),
            execution_bar_types=tuple(execution_types),
            min_prominence_atr=args.min_prominence_atr,
            enable_rejection=not args.acceptance_only,
            enable_acceptance=not args.rejection_only,
            trading_start_ns=int(pd.Timestamp(args.start, tz="UTC").value),
        ),
    )
    engine.add_strategy(strategy)

    try:
        engine.run()
        fills = engine.trader.generate_order_fills_report()
        orders = engine.trader.generate_orders_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(VENUE)
        fills.to_csv(args.output / "fills.csv", index=False)
        orders.to_csv(args.output / "orders.csv", index=False)
        positions.to_csv(args.output / "positions.csv", index=False)
        account.to_csv(args.output / "account.csv", index=False)

        with (args.output / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in strategy.event_log:
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

        nav = final_nav(engine)
        days = (args.end - args.start).days + 1
        daily_geo = (nav / STARTING_NAV) ** (1.0 / days) - 1.0 if nav > 0 else -1.0
        closed = sum(event.get("kind") == "position_closed" for event in strategy.event_log)
        plans = [event for event in strategy.event_log if event.get("kind") == "plan"]
        submitted = [event for event in strategy.event_log if event.get("kind") == "submitted"]
        metrics = {
            "candidate": "candidate-easychart-v2",
            "start": args.start.isoformat(),
            "end": args.end.isoformat(),
            "symbols": list(symbols),
            "starting_nav": STAUS‘×ÓU‹ˆ™š[˜[Û˜]ˆˆ˜]‹ˆİ[Ü™]\›ˆˆ˜]ˆÈÕUD”äuôäbÒãÀ¢&F–Ç•övVöÖWG&–5öw&÷wF‚#¢F–Ç•övVòÀ¢&6ÆVæF%öF—2#¢F—2À¢&f–ÆÇ2#¢–çB†ÆVâ†f–ÆÇ2æ–æFW‚’’À¢&6Æ÷6VE÷÷6—F–öç2#¢6Æ÷6VBÀ¢'Æç2#¢ÆVâ‡Æç2’À¢'7V&Ö—GFVE÷Æç2#¢ÆVâ‡7V&Ö—GFVB’À¢&–æFWVæFVçE÷G&FW5÷W%öF’#¢6Æ÷6VBòF—2À¢'&—6µög&7F–öâ#¢ã2À¢&Ö–æ–×VÕöw&÷75÷'"#¢ãÀ¢&Ö–å÷&öÖ–æVæ6UöG"#¢&w2æÖ–å÷&öÖ–æVæ6UöG"À¢&Væ&ÆU÷&V¦V7F–öâ#¢æ÷B&w2æ66WFæ6UööæÇ’À¢&Væ&ÆUö66WFæ6R#¢æ÷B&w2ç&V¦V7F–öåööæÇ’À¢&F–væ÷7F–72#¢°¢7–Ö&öÃ¢7G&FVw’æVæv–æW5¶–ç7G'VÖVçBæ–EÒæF–væ÷7F–70¢f÷"7–Ö&öÂÂ–ç7G'VÖVçB–â¦—‡7–Ö&öÇ2Â–ç7G'VÖVçG2Â7G&–7CÕG'VR¢ÒÀ¢Ğ¢w&—FUö§6öâ†&w2æ÷WGWBò&ÖWG&–72æ§6öâ"ÂÖWG&–72¢w&—FUö§6öâ€¢&w2æ÷WGWBò''Vâæ§6öâ"À¢°¢''Våö–B#¢b&V7c"×¶FFWF–ÖRææ÷r‡F–ÖW¦öæRçWF2’ç7G&gF–ÖR‚rU’VÒVEBT‚TÒU5¢r—Ò"À¢&6æF–FFR#¢&6æF–FFRÖV7–6†'B×c""À¢&Væv–æR#¢$æWF–ÇW5G&FW"&6·FW7DVæv–æR"À¢&FF#¢$&–ææ6Rf—6–öâU4BÔÒÒ¶Æ–æW3²æWF–ÇW2VÒ6ö×÷6—FR6–væÇ2æBÒW†V7WF–öâ"À¢&6öçG&7B#¢°¢'6–ævÆUöVçG'’#¢G'VRÀ¢'6–ævÆUögVÆÅ÷7F÷öÖ&¶WB#¢G'VRÀ¢'6–ævÆUögVÆÅ÷F&vWB#¢G'VRÀ¢'&—6µög&7F–öåö7W'&VçEöæb#¢ã2À¢&Ö–å÷&UöVçG'•öw&÷75÷'"#¢ãÀ¢&vÆö&Å÷VæF–æuö÷%÷÷6—F–öåöÆ–Ö—B#¢À¢''F–ÅöÖævVÖVçB#¢fÇ6RÀ¢&F–Ç•öÆ÷75öÆ–Ö—B#¢æöæRÀ¢'G&FUö6÷VçEöÆ–Ö—B#¢æöæRÀ¢ÒÀ¢ÒÀ¢¢&–çB†§6öâæGV×2†ÖWG&–72ÂVç7W&Uö66–“ÔfÇ6RÂ–æFVçCÓ"Â6÷'Eö¶W—3ÕG'VR’¢f–æÆÇ“ ¢Væv–æRæF—7÷6R‚  ¦–bõöæÖUõòÓÒ%õöÖ–åõò# ¢Ö–â‚ 