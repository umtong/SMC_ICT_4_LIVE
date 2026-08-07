#!/usr/bin/env python3
"""Deterministically derive candidate-09 v25 from the frozen v24 direct source.

This is a transport/build helper only. It does not inspect PnL or change numerical
thresholds. The economic change is spot-led confirmation and source-auction equilibrium.
"""
from __future__ import annotations

import base64
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build_engine() -> None:
    s = (ROOT / "state_engine_v24_direct.py").read_text(encoding="utf-8")
    replacements = [
        ("v24: index-anchored liquidation-dislocation reversion", "v25: spot-led liquidation-dislocation auction reversion"),
        ("Binance index-price auction", "Binance spot auction"),
        ("futures/index dislocation", "perpetual/spot dislocation"),
        ("frozen pre-shock fair\n# basis", "frozen pre-shock source-auction equilibrium"),
        ("Exact controls remove OI, remove only the futures/index dislocation admission test, or\nremove only post-shock basis-reclaim confirmation.", "Exact controls remove OI, remove only the perpetual/spot dislocation admission test, or\nremove only spot-led reversal confirmation."),
        ("index_open", "spot_open"), ("index_high", "spot_high"),
        ("index_low", "spot_low"), ("index_close", "spot_close"),
        ("index_values", "spot_values"), ("index OHLC", "spot OHLC"),
        ("has_index", "has_spot"), ("require_index_gap", "require_spot_gap"),
        ("no-index-gap", "no-spot-gap"), ("require_reclaim", "require_spot_lead"),
        ("no-reclaim", "no-spot-lead"), ("index_pulse_high", "spot_pulse_high"),
        ("index_pulse_low", "spot_pulse_low"), ("frozen_index_atr", "frozen_spot_atr"),
        ("_index_atr", "_spot_atr"), ("index_return", "spot_return"),
        ("index_ok", "spot_gap_ok"), ("_index_dislocation_reclaimed", "_spot_led_reversal_confirmed"),
        ("INDEX_PRICE_UNAVAILABLE_AT_ENTRY", "SPOT_PRICE_UNAVAILABLE_AT_ENTRY"),
        ("index-dislocation", "spot-dislocation"),
        ("INDEX_DISLOCATION", "SPOT_DISLOCATION"),
        ("INDEX_LIQUIDATION", "SPOT_LIQUIDATION"),
        ("INDEX_ANCHORED", "SPOT_LED"),
        ("index-price", "spot"), ("index price", "spot price"), ("index", "spot"),
    ]
    for old, new in replacements:
        s = s.replace(old, new)
    s = s.replace("detected_spot", "detected_index").replace("self._spot", "self._index")
    s = s.replace("self._index_atr(", "self._spot_atr(")
    s = s.replace(
        "one-minute internal structure shift before entering toward the frozen pre-shock fair\nbasis.",
        "completed spot-led reversal, perpetual counterflow and basis contraction before entering\ntoward the frozen pre-shock source-auction equilibrium.",
    )
    s = s.replace("remove only post-shock basis-reclaim confirmation.", "remove only the spot-lead component of post-shock reversal confirmation.")
    s = s.replace('"""OI-qualified perpetual/spot dislocation followed by causal basis reversion."""', '"""OI-qualified perpetual/spot dislocation followed by spot-led auction reversion."""')
    old = '''            self._pending = candidate
            if not self.config.require_spot_lead:
                signal, reason = self._build_signal(candidate, bar, confirmation="pulse-close-control")
                signal = self._finish(candidate, bar, signal, reason, events)
'''
    if old not in s:
        raise RuntimeError("v24 pending block changed")
    s = s.replace(old, "            self._pending = candidate\n")
    start = s.index("    def _advance_pending(")
    end = s.index("    def _build_signal(", start)
    block = '''    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        if self._index <= pending.detected_index:
            return None
        pending.observed_high = max(pending.observed_high, bar.high)
        pending.observed_low = min(pending.observed_low, bar.low)
        elapsed = self._index - pending.detected_index
        if self._source_target_reached(pending, bar):
            self._expire(pending, bar, "SOURCE_AUCTION_EQUILIBRIUM_REACHED_BEFORE_ENTRY", events)
            return None
        if self._spot_led_reversal_confirmed(pending, bar):
            signal, reason = self._build_signal(pending, bar, confirmation="basis-contraction-plus-spot-lead-plus-perpetual-counterflow")
            return self._finish(pending, bar, signal, reason, events)
        if elapsed >= self.config.confirmation_timeout_bars:
            self._expire(pending, bar, "SPOT_LED_REVERSAL_DID_NOT_CONFIRM_IN_TIME", events)
        return None

    @staticmethod
    def _source_target(pending: _PendingDislocation) -> float:
        return pending.source.equilibrium

    def _source_target_reached(self, pending: _PendingDislocation, bar: FlowBar) -> bool:
        target = self._source_target(pending)
        return bar.high >= target if pending.direction == "DOWN" else bar.low <= target

    def _spot_led_reversal_confirmed(self, pending: _PendingDislocation, bar: FlowBar) -> bool:
        if bar.spot_close is None or len(self._bars) < 2:
            return False
        previous = self._bars[-2]
        if not previous.has_spot:
            return False
        assert previous.spot_high is not None and previous.spot_low is not None
        assert bar.spot_open is not None and bar.spot_high is not None and bar.spot_low is not None
        current = bar.close / bar.spot_close - 1.0 - pending.fair_basis
        initial = pending.initial_basis_dislocation
        contracted = abs(current) <= (1.0 - self.config.basis_reclaim_fraction) * abs(initial)
        toward_fair = current > initial if pending.direction == "DOWN" else current < initial
        futures_body_atr = abs(bar.close - bar.open) / max(pending.frozen_atr, 1e-12)
        spot_body_atr = abs(bar.spot_close - bar.spot_open) / max(pending.frozen_spot_atr, 1e-12)
        minimum_body = self.config.minimum_resolution_displacement_atr
        if pending.direction == "DOWN":
            perpetual_counterflow = bar.close > self._bars[-2].high and bar.close > bar.open
            flow_shift = bar.flow_imbalance >= self.config.directional_imbalance
            spot_lead = bar.spot_close > previous.spot_high and bar.spot_close > bar.spot_open
            spot_not_extending = bar.spot_low >= pending.spot_pulse_low - 0.10 * pending.frozen_spot_atr
            futures_not_extending = bar.low >= pending.pulse_low - 0.10 * pending.frozen_atr
        else:
            perpetual_counterflow = bar.close < self._bars[-2].low and bar.close < bar.open
            flow_shift = bar.flow_imbalance <= -self.config.directional_imbalance
            spot_lead = bar.spot_close < previous.spot_low and bar.spot_close < bar.spot_open
            spot_not_extending = bar.spot_high <= pending.spot_pulse_high + 0.10 * pending.frozen_spot_atr
            futures_not_extending = bar.high <= pending.pulse_high + 0.10 * pending.frozen_atr
        return (contracted and toward_fair and perpetual_counterflow and flow_shift
                and (spot_lead if self.config.require_spot_lead else True)
                and spot_not_extending and futures_not_extending
                and futures_body_atr >= minimum_body
                and (spot_body_atr >= minimum_body if self.config.require_spot_lead else True))

'''
    s = s[:start] + block + s[end:]
    s = s.replace('''        target = self._fair_target(pending, bar)
        if target is None:
            return None, "SPOT_PRICE_UNAVAILABLE_AT_ENTRY"
''', '        target = self._source_target(pending)\n')
    for old, new in {
        "SPOT_DISLOCATION_REVERSION_HAS_INVALID_GEOMETRY": "SPOT_LED_AUCTION_REVERSION_HAS_INVALID_GEOMETRY",
        "SPOT_DISLOCATION_REVERSION_HAS_NONPOSITIVE_REWARD_AFTER_COST": "SPOT_LED_AUCTION_REVERSION_HAS_NONPOSITIVE_REWARD_AFTER_COST",
        "SPOT_DISLOCATION_REVERSION_NET_REWARD_TO_RISK_BELOW_GATE": "SPOT_LED_AUCTION_REVERSION_NET_REWARD_TO_RISK_BELOW_GATE",
        "SPOT_LED_LIQUIDATION_DISLOCATION_REVERSION": "SPOT_LED_LIQUIDATION_AUCTION_REVERSION",
        '"fair_basis_target": target,': '"source_auction_equilibrium_target": target,',
        '"SPOT_DISLOCATION_WITHOUT_OI_CONTROL"': '"SPOT_DISLOCATION_WITHOUT_OI_CONTROL"',
        '"ABNORMAL_OI_DROP_WITHOUT_SPOT_DISLOCATION_CONTROL"': '"ABNORMAL_OI_DROP_WITHOUT_SPOT_GAP_CONTROL"',
        '"ABNORMAL_OI_DROP_WITH_FUTURES_SPOT_DISLOCATION"': '"ABNORMAL_OI_DROP_WITH_PERPETUAL_SPOT_DISLOCATION"',
        '"SPOT_LIQUIDATION_DISLOCATION_CONFIRMED"': '"SPOT_LED_LIQUIDATION_DISLOCATION_CONFIRMED"',
        '"BASIS_RECLAIM_PENDING" if self.config.require_spot_lead else "ENTERABLE_CONTROL"': '"SPOT_LED_REVERSAL_PENDING"',
        '"SPOT_DISLOCATION_REVERSION_CONFIRMED" if signal is not None else "SPOT_DISLOCATION_REVERSION_UNTRADEABLE"': '"SPOT_LED_AUCTION_REVERSION_CONFIRMED" if signal is not None else "SPOT_LED_AUCTION_REVERSION_UNTRADEABLE"',
        '"BASIS_RECLAIM_PENDING",': '"SPOT_LED_REVERSAL_PENDING",',
        '"SPOT_DISLOCATION_REVERSION_EXPIRED"': '"SPOT_LED_AUCTION_REVERSION_EXPIRED"',
        '"fair_basis_target"': '"source_auction_equilibrium_target"',
    }.items():
        s = s.replace(old, new)
    (ROOT / "state_engine_v25_direct.py").write_text(s, encoding="utf-8")


def build_loader() -> None:
    s = (ROOT / "data_loader_v24.py").read_text(encoding="utf-8")
    for old, new in [
        ("USD-M futures, index-price and positioning", "USD-M futures, Binance spot and positioning"),
        ("The traded BTCUSDT perpetual kline and the Binance BTCUSDT index-price kline are joined", "The traded BTCUSDT perpetual kline and the Binance BTCUSDT spot kline are joined"),
        ("index, price and positioning", "spot, futures and positioning"),
        ("candidate-09-v24", "candidate-09-v25"), ("class IndexBar:", "class SpotBar:"),
        ("bars_without_index", "bars_without_spot"), ("parse_index_archive", "parse_spot_archive"),
        ("IndexBar", "SpotBar"), ("index_bars", "spot_bars"), ("index_by_time", "spot_by_time"),
        ("duplicate index-price timestamp", "duplicate spot timestamp"),
        ("index_open=", "spot_open="), ("index_high=", "spot_high="),
        ("index_low=", "spot_low="), ("index_close=", "spot_close="),
        ("index.open if index is not None", "spot.open if spot is not None"),
        ("index.high if index is not None", "spot.high if spot is not None"),
        ("index.low if index is not None", "spot.low if spot is not None"),
        ("index.close if index is not None", "spot.close if spot is not None"),
        ("index = spot_by_time.get(bar.ts_ns)", "spot = spot_by_time.get(bar.ts_ns)"),
        ("index-price", "spot"), ("index price", "spot price"),
        ("index_price_klines", "spot_klines"), ("_index_daily", "_spot_daily"),
        ("_index_monthly", "_spot_monthly"), ("index_record", "spot_record"),
        ("indexPriceKlines", "klines"),
        (' / self.symbol / "klines" / self.interval /', ' / "spot" / self.symbol / self.interval /'),
        ("not bar.has_index", "not bar.has_spot"), ("index", "spot"),
    ]:
        s = s.replace(old, new)
    s = s.replace('BASE_URL = "https://data.binance.vision/data/futures/um"', 'FUTURES_BASE_URL = "https://data.binance.vision/data/futures/um"\nSPOT_BASE_URL = "https://data.binance.vision/data/spot"')
    s = s.replace('f"{BASE_URL}/daily/klines/{self.symbol}/{self.interval}/{filename}"', 'f"{FUTURES_BASE_URL}/daily/klines/{self.symbol}/{self.interval}/{filename}"', 1)
    s = s.replace('f"{BASE_URL}/daily/metrics/{self.symbol}/{filename}"', 'f"{FUTURES_BASE_URL}/daily/metrics/{self.symbol}/{filename}"')
    s = s.replace('f"{BASE_URL}/monthly/klines/{self.symbol}/{self.interval}/{filename}"', 'f"{FUTURES_BASE_URL}/monthly/klines/{self.symbol}/{self.interval}/{filename}"', 1)
    s = s.replace('f"{BASE_URL}/monthly/metrics/{self.symbol}/{filename}"', 'f"{FUTURES_BASE_URL}/monthly/metrics/{self.symbol}/{filename}"')
    s = s.replace('f"{BASE_URL}/daily/klines/{self.symbol}/{self.interval}/{filename}"', 'f"{SPOT_BASE_URL}/daily/klines/{self.symbol}/{self.interval}/{filename}"')
    s = s.replace('f"{BASE_URL}/monthly/klines/{self.symbol}/{self.interval}/{filename}"', 'f"{SPOT_BASE_URL}/monthly/klines/{self.symbol}/{self.interval}/{filename}"')
    s = s.replace("metric_spot", "metric_index").replace("# type: ignore[spot]", "# type: ignore[index]")
    (ROOT / "data_loader_v25.py").write_text(s, encoding="utf-8")


def build_run_and_config() -> None:
    s = (ROOT / "run_v24_direct.py").read_text(encoding="utf-8")
    s = s.replace("candidate-09 v24", "candidate-09 v25").replace("candidate-09-v24", "candidate-09-v25")
    s = s.replace("futures/index dislocation", "perpetual/spot dislocation")
    s = s.replace("the basis contracts and a completed one-minute internal structure shift\nconfirms reversion toward the frozen fair basis.", "the basis contracts, completed spot price leads the reversal, and perpetual counterflow\nconfirms reversion toward the frozen source-auction equilibrium.")
    s = s.replace("the index-dislocation admission test, or remove only post-shock reclaim confirmation", "the spot-dislocation admission test, or remove only the spot-lead confirmation")
    s = s.replace('''ABLATIONS = (
    "baseline",
    "no-oi",
    "no-index-gap",
    "no-reclaim",
)''', '''ABLATIONS = (
    "baseline",
    "no-oi",
    "no-spot-gap",
    "no-spot-lead",
)''')
    s = s.replace("index-anchored dislocation reversion branch produced executable events", "spot-led liquidation auction reversion branch produced executable events")
    s = s.replace("Binance index-price kline is a completed fair-value anchor, not an executable spot quote", "Binance spot kline is a completed external price-discovery observation, not the traded execution venue")
    s = s.replace("Futures and index bars are aligned", "Perpetual and spot bars are aligned").replace("futures/index", "perpetual/spot").replace("index bars", "spot bars")
    needle = '    output = args.output.resolve()\n    output.mkdir(parents=True, exist_ok=True)\n'
    s = s.replace(needle, needle + '    for stale_name in ("summary.json", "REPORT.md", "outcomes.csv", "trades.csv", "fills.csv", "run.json", "data_manifest.json", "events.jsonl", "event_summary.json", "trade_summary.json", "compact_manifest.json"):\n        (output / stale_name).unlink(missing_ok=True)\n')
    needle = '    write_csv(output / "fills.csv", baseline_fills)\n    write_csv(output / "outcomes.csv", [asdict(detail.outcome) for detail in all_details])\n'
    insert = needle + '''    write_json(output / "event_summary.json", {
        "rows": len(baseline_events),
        "event_type_counts": dict(Counter(str(row.get("event_type")) for row in baseline_events)),
        "reason_code_counts": dict(Counter(str(row.get("reason_code")) for row in baseline_events)),
        "next_state_counts": dict(Counter(str(row.get("next_state")) for row in baseline_events)),
        "run_event_counts": dict(Counter(str(row.get("run_id")) for row in baseline_events)),
    })
    trade_groups: dict[str, dict[str, Any]] = {}
    for row in baseline_trades:
        key = f"{row.get('branch')}|{row.get('reason_code')}"
        group = trade_groups.setdefault(key, {"trades": 0, "wins": 0, "net_pnl": 0.0})
        pnl = float(row.get("net_pnl", 0.0))
        group["trades"] += 1; group["wins"] += int(pnl > 0.0); group["net_pnl"] += pnl
    write_json(output / "trade_summary.json", {"groups": trade_groups})
'''
    s = s.replace(needle, insert)
    (ROOT / "run_v25_direct.py").write_text(s, encoding="utf-8")
    config = json.loads((ROOT / "config_v24.json").read_text(encoding="utf-8"))
    config["candidate"] = "candidate-09-v25"
    (ROOT / "config_v25.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_support() -> None:
    payload = "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"
    archive = ROOT / ".v25-support.tar.gz"
    archive.write_bytes(base64.b64decode(payload))
    with tarfile.open(archive, "r:gz") as handle:
        handle.extractall(ROOT)
    archive.unlink()


if __name__ == "__main__":
    build_engine(); build_loader(); build_run_and_config(); extract_support()
