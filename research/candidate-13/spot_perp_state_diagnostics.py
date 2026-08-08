#!/usr/bin/env python3
"""Causal spot/perpetual state diagnostics for Candidate 13 V11.

The script does not create orders or resimulate PnL.  It joins the already
materialized NautilusTrader trade ledger to Binance public spot and USD-M
perpetual one-minute bars using only timestamps visible when each V11 plan was
observed.  A predeclared sign/order policy routes each V11 attempted initiative
into FAILED_AUCTION, CONTINUATION, or UNRESOLVED.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from math import isfinite, log
from pathlib import Path
import re
import time
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pandas as pd

UTC = timezone.utc
MINUTE_NS = 60_000_000_000
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
QHF_MODULE = "QUARTER_HOUR_FAILED_INITIATIVE_REVERSAL"
COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
EVENT_ID_RE = re.compile(r"^QHE-(\d+)-\d+-(?:LONG|SHORT)-[A-Z0-9]+$")


@dataclass(frozen=True, slots=True)
class Window:
    open: float
    close: float
    volume: float
    taker_buy_volume: float

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))


@dataclass(frozen=True, slots=True)
class StateObservation:
    state: str
    reason: str
    features: dict[str, Any]
    flags: dict[str, bool]


def _decimal(value: Any) -> Decimal:
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return Decimal("0")
    return Decimal(text.split()[0])


def _download(url: str, destination: Path, retries: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 100:
        try:
            with ZipFile(destination) as archive:
                if archive.testzip() is None:
                    return
        except Exception:
            destination.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-13-v12-diagnostics"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=90) as response:  # noqa: S310 fixed HTTPS host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"unexpectedly small response from {url}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            with ZipFile(temporary) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt ZIP member {bad}")
            temporary.replace(destination)
            return
        except Exception as exc:  # pragma: no cover - network retry evidence
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def _timestamp_unit(value: int) -> tuple[str, int]:
    if 1_000_000_000_000 <= value < 10_000_000_000_000:
        return "ms", 1_000_000
    if 1_000_000_000_000_000 <= value < 10_000_000_000_000_000:
        return "us", 1_000
    if 1_000_000_000_000_000_000 <= value < 10_000_000_000_000_000_000:
        return "ns", 1
    raise RuntimeError(f"unsupported timestamp magnitude: {value}")


def load_monthly_bars(
    *,
    symbol: str,
    market: str,
    month: str,
    data_dir: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if market == "spot":
        prefix = "spot"
        url = (
            "https://data.binance.vision/data/spot/monthly/klines/"
            f"{symbol}/1m/{symbol}-1m-{month}.zip"
        )
    elif market == "perp":
        prefix = "futures-um"
        url = (
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            f"{symbol}/1m/{symbol}-1m-{month}.zip"
        )
    else:
        raise ValueError(f"unsupported market: {market}")
    path = data_dir / prefix / symbol / f"{symbol}-1m-{month}.zip"
    _download(url, path)
    digest = sha256(path.read_bytes()).hexdigest()
    with ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members: {path}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if set(COLUMNS).issubset(frame.columns):
        frame = frame.loc[:, COLUMNS]
    else:
        frame = pd.read_csv(BytesIO(payload), header=None, names=COLUMNS)
    numeric_time = pd.to_numeric(frame["open_time"], errors="coerce")
    valid = numeric_time.notna()
    frame = frame.loc[valid].copy()
    frame["open_time"] = numeric_time.loc[valid].astype("int64")
    if frame.empty:
        raise RuntimeError(f"empty archive after timestamp normalization: {path}")
    _, factor = _timestamp_unit(int(frame["open_time"].iloc[0]))
    open_ns = frame["open_time"].to_numpy(dtype="int64") * factor
    visible_ns = open_ns + MINUTE_NS
    result = pd.DataFrame(index=pd.Index(visible_ns, name="visible_ts_ns"))
    for name in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        result[name] = pd.to_numeric(frame[name], errors="raise").to_numpy(dtype="float64")
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result, {
        "symbol": symbol,
        "market": market,
        "month": month,
        "url": url,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest,
        "rows": int(len(result.index)),
    }


def _row(frame: pd.DataFrame, ts_ns: int) -> pd.Series | None:
    try:
        row = frame.loc[ts_ns]
    except KeyError:
        return None
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    return row


def _window(frame: pd.DataFrame, end_ts_ns: int, bars: int = 5) -> Window | None:
    stamps = [end_ts_ns - (bars - 1 - offset) * MINUTE_NS for offset in range(bars)]
    rows = [_row(frame, stamp) for stamp in stamps]
    if any(item is None for item in rows):
        return None
    materialized = [item for item in rows if item is not None]
    return Window(
        open=float(materialized[0]["open"]),
        close=float(materialized[-1]["close"]),
        volume=sum(float(item["volume"]) for item in materialized),
        taker_buy_volume=sum(float(item["taker_buy_volume"]) for item in materialized),
    )


def _safe_log_ratio(new: float, old: float) -> float:
    if new <= 0.0 or old <= 0.0:
        raise ValueError("price must be positive")
    return log(new / old)


def classify_plan(
    plan: dict[str, Any],
    *,
    spot: pd.DataFrame,
    perp: pd.DataFrame,
) -> StateObservation:
    details = plan.get("details", {})
    if plan.get("module") != QHF_MODULE:
        return StateObservation(
            state="OTHER_MODULE",
            reason="NOT_QHF_MODULE",
            features={},
            flags={},
        )
    event_id = str(details.get("second_event_id", ""))
    match = EVENT_ID_RE.match(event_id)
    if match is None:
        return StateObservation(
            state="UNRESOLVED",
            reason="SECOND_EVENT_ID_UNPARSABLE",
            features={"second_event_id": event_id},
            flags={},
        )
    second_ts_ns = int(match.group(1))
    observed_ts_ns = int(plan["observed_ts_ns"])
    if second_ts_ns > observed_ts_ns:
        return StateObservation(
            state="UNRESOLVED",
            reason="NON_CAUSAL_SECOND_EVENT_TIMESTAMP",
            features={"second_ts_ns": second_ts_ns, "observed_ts_ns": observed_ts_ns},
            flags={},
        )
    spot_event = _window(spot, second_ts_ns)
    perp_event = _window(perp, second_ts_ns)
    spot_confirm = _window(spot, observed_ts_ns)
    perp_confirm = _window(perp, observed_ts_ns)
    spot_event_close = _row(spot, second_ts_ns)
    perp_event_close = _row(perp, second_ts_ns)
    spot_observed = _row(spot, observed_ts_ns)
    perp_observed = _row(perp, observed_ts_ns)
    if any(
        item is None
        for item in (
            spot_event,
            perp_event,
            spot_confirm,
            perp_confirm,
            spot_event_close,
            perp_event_close,
            spot_observed,
            perp_observed,
        )
    ):
        return StateObservation(
            state="UNRESOLVED",
            reason="CAUSAL_SPOT_PERP_WINDOW_MISSING",
            features={"second_ts_ns": second_ts_ns, "observed_ts_ns": observed_ts_ns},
            flags={},
        )

    failed_direction = str(details.get("failed_direction", ""))
    reversal_direction = str(details.get("reversal_direction", plan.get("direction", "")))
    failed_sign = 1.0 if failed_direction == "LONG" else -1.0 if failed_direction == "SHORT" else 0.0
    reversal_sign = 1.0 if reversal_direction == "LONG" else -1.0 if reversal_direction == "SHORT" else 0.0
    if failed_sign == 0.0 or reversal_sign != -failed_sign:
        return StateObservation(
            state="UNRESOLVED",
            reason="DIRECTION_CONTRACT_INVALID",
            features={
                "failed_direction": failed_direction,
                "reversal_direction": reversal_direction,
            },
            flags={},
        )

    assert spot_event is not None and perp_event is not None
    assert spot_confirm is not None and perp_confirm is not None
    assert spot_event_close is not None and perp_event_close is not None
    assert spot_observed is not None and perp_observed is not None

    spot_attempt_return = failed_sign * _safe_log_ratio(spot_event.close, spot_event.open)
    perp_attempt_return = failed_sign * _safe_log_ratio(perp_event.close, perp_event.open)
    spot_attempt_flow = failed_sign * spot_event.signed_flow
    perp_attempt_flow = failed_sign * perp_event.signed_flow
    basis_start = _safe_log_ratio(perp_event.open, spot_event.open)
    basis_event = _safe_log_ratio(perp_event.close, spot_event.close)
    basis_observed = _safe_log_ratio(
        float(perp_observed["close"]),
        float(spot_observed["close"]),
    )
    basis_expansion_attempt = failed_sign * (basis_event - basis_start)
    basis_reversion = reversal_sign * (basis_observed - basis_event)
    spot_reversal_return = reversal_sign * _safe_log_ratio(
        float(spot_observed["close"]),
        float(spot_event_close["close"]),
    )
    perp_reversal_return = reversal_sign * _safe_log_ratio(
        float(perp_observed["close"]),
        float(perp_event_close["close"]),
    )
    spot_reversal_flow = reversal_sign * spot_confirm.signed_flow
    perp_reversal_flow = reversal_sign * perp_confirm.signed_flow
    spot_continuation_return = failed_sign * _safe_log_ratio(
        float(spot_observed["close"]),
        float(spot_event_close["close"]),
    )

    flags = {
        "attempt_directional_in_perp": perp_attempt_return > 0.0,
        "attempt_perp_led_price": perp_attempt_return > max(spot_attempt_return, 0.0),
        "attempt_perp_led_flow": perp_attempt_flow > max(spot_attempt_flow, 0.0),
        "attempt_basis_expanded": basis_expansion_attempt > 0.0,
        "basis_reverted_toward_reversal": basis_reversion > 0.0,
        "spot_confirmed_reversal_price": spot_reversal_return > 0.0,
        "spot_confirmed_reversal_flow": spot_reversal_flow > 0.0,
        "perp_confirmed_reversal_price": perp_reversal_return > 0.0,
        "spot_backed_attempt_price": spot_attempt_return > 0.0,
        "spot_backed_attempt_flow": spot_attempt_flow > 0.0,
        "spot_still_continued_attempt": spot_continuation_return > 0.0,
    }
    derivative_led_attempt = all(
        flags[name]
        for name in (
            "attempt_directional_in_perp",
            "attempt_perp_led_price",
            "attempt_perp_led_flow",
            "attempt_basis_expanded",
        )
    )
    confirmed_failure = all(
        flags[name]
        for name in (
            "basis_reverted_toward_reversal",
            "spot_confirmed_reversal_price",
            "spot_confirmed_reversal_flow",
            "perp_confirmed_reversal_price",
        )
    )
    spot_backed_continuation = all(
        flags[name]
        for name in (
            "spot_backed_attempt_price",
            "spot_backed_attempt_flow",
            "spot_still_continued_attempt",
        )
    ) and not flags["basis_reverted_toward_reversal"]

    if derivative_led_attempt and confirmed_failure:
        state, reason = "FAILED_AUCTION", "DERIVATIVE_LED_ATTEMPT_AND_SPOT_BASIS_REVERSAL"
    elif spot_backed_continuation:
        state, reason = "CONTINUATION", "SPOT_BACKED_ATTEMPT_REMAINS_ACCEPTED"
    else:
        state, reason = "UNRESOLVED", "SPOT_PERP_EVIDENCE_NOT_MUTUALLY_DECISIVE"

    features = {
        "second_ts_ns": second_ts_ns,
        "observed_ts_ns": observed_ts_ns,
        "latency_minutes": (observed_ts_ns - second_ts_ns) / MINUTE_NS,
        "spot_attempt_return": spot_attempt_return,
        "perp_attempt_return": perp_attempt_return,
        "attempt_price_lead_spread": perp_attempt_return - spot_attempt_return,
        "spot_attempt_flow": spot_attempt_flow,
        "perp_attempt_flow": perp_attempt_flow,
        "attempt_flow_lead_spread": perp_attempt_flow - spot_attempt_flow,
        "basis_start": basis_start,
        "basis_event": basis_event,
        "basis_observed": basis_observed,
        "basis_expansion_attempt": basis_expansion_attempt,
        "basis_reversion": basis_reversion,
        "spot_reversal_return": spot_reversal_return,
        "perp_reversal_return": perp_reversal_return,
        "spot_reversal_flow": spot_reversal_flow,
        "perp_reversal_flow": perp_reversal_flow,
    }
    if not all(isfinite(float(value)) for value in features.values()):
        return StateObservation(
            state="UNRESOLVED",
            reason="NONFINITE_SPOT_PERP_FEATURE",
            features=features,
            flags=flags,
        )
    return StateObservation(state=state, reason=reason, features=features, flags=flags)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _map_positions(
    lifecycle_path: Path,
    positions_path: Path,
) -> dict[str, dict[str, Any]]:
    lifecycle = _load_json(lifecycle_path).get("events", [])
    fills = [item for item in lifecycle if item.get("type") == "GLOBAL_ENTRY_FILLED"]
    positions = pd.read_csv(positions_path)
    if positions.empty and not fills:
        return {}
    positions = positions.sort_values("ts_opened", kind="stable").reset_index(drop=True)
    if len(fills) != len(positions.index):
        raise RuntimeError(
            f"global fill/position mismatch for {positions_path}: "
            f"fills={len(fills)} positions={len(positions.index)}",
        )
    mapped: dict[str, dict[str, Any]] = {}
    for event, (_, position) in zip(fills, positions.iterrows(), strict=True):
        symbol = str(position["instrument_id"]).split("-PERP", 1)[0]
        if symbol != str(event.get("symbol")):
            raise RuntimeError(
                f"fill/position symbol mismatch: event={event.get('symbol')} position={symbol}",
            )
        mapped[str(event["scenario_id"])] = {
            "symbol": symbol,
            "pnl": float(_decimal(position["realized_pnl"])),
            "duration_ns": int(position["duration_ns"]),
            "ts_opened": str(position["ts_opened"]),
            "ts_closed": str(position["ts_closed"]),
            "peak_qty": str(position["peak_qty"]),
        }
    return mapped


def _payoff(pnls: list[float]) -> float | None:
    wins = [value for value in pnls if value > 0.0]
    losses = [value for value in pnls if value < 0.0]
    if wins and losses:
        return (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
    if wins:
        return float("inf")
    return None


def _summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(records)
    filled = [item for item in materialized if item.get("position") is not None]
    pnls = [float(item["position"]["pnl"]) for item in filled]
    wins = [value for value in pnls if value > 0.0]
    losses = [value for value in pnls if value < 0.0]
    unique_episodes = {
        str(item.get("independent_episode_key"))
        for item in materialized
        if item.get("independent_episode_key")
    }
    filled_episodes = {
        str(item.get("independent_episode_key"))
        for item in filled
        if item.get("independent_episode_key")
    }
    payoff = _payoff(pnls)
    return {
        "plans": len(materialized),
        "unique_episodes": len(unique_episodes),
        "filled": len(filled),
        "filled_unique_episodes": len(filled_episodes),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "realized_pnl": sum(pnls),
        "payoff_ratio": None if payoff == float("inf") else payoff,
        "mean_duration_minutes": (
            sum(float(item["position"]["duration_ns"]) for item in filled)
            / len(filled)
            / MINUTE_NS
            if filled
            else None
        ),
    }


def analyze_interval(
    *,
    interval: str,
    spec: dict[str, Any],
    results_root: Path,
    data_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    result_dir = results_root / interval
    plans = _load_json(result_dir / "submitted_plans.json").get("plans", [])
    positions = _map_positions(
        result_dir / "order_lifecycle.json",
        result_dir / "positions.csv",
    )
    months: set[str] = set()
    for plan in plans:
        observed = datetime.fromtimestamp(int(plan["observed_ts_ns"]) / 1_000_000_000, tz=UTC)
        months.add(observed.strftime("%Y-%m"))
        details = plan.get("details", {})
        match = EVENT_ID_RE.match(str(details.get("second_event_id", "")))
        if match:
            second = datetime.fromtimestamp(int(match.group(1)) / 1_000_000_000, tz=UTC)
            months.add(second.strftime("%Y-%m"))

    bars: dict[tuple[str, str], pd.DataFrame] = {}
    manifests: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        for market in ("spot", "perp"):
            frames: list[pd.DataFrame] = []
            for month in sorted(months):
                frame, manifest = load_monthly_bars(
                    symbol=symbol,
                    market=market,
                    month=month,
                    data_dir=data_dir,
                )
                frames.append(frame)
                manifests.append(manifest)
            if frames:
                merged = pd.concat(frames).sort_index()
                merged = merged[~merged.index.duplicated(keep="last")]
            else:
                merged = pd.DataFrame(columns=("open", "high", "low", "close", "volume", "taker_buy_volume"))
            bars[(market, symbol)] = merged

    records: list[dict[str, Any]] = []
    for plan in plans:
        symbol = str(plan["symbol"])
        observation = classify_plan(
            plan,
            spot=bars[("spot", symbol)],
            perp=bars[("perp", symbol)],
        )
        records.append({
            "interval": interval,
            "role": spec.get("role"),
            "scenario_id": plan["scenario_id"],
            "symbol": symbol,
            "module": plan.get("module"),
            "direction": plan.get("direction"),
            "failed_direction": plan.get("details", {}).get("failed_direction"),
            "independent_episode_key": plan.get("details", {}).get("independent_episode_key"),
            "observed_ts_ns": int(plan["observed_ts_ns"]),
            "state": observation.state,
            "state_reason": observation.reason,
            "features": observation.features,
            "flags": observation.flags,
            "position": positions.get(str(plan["scenario_id"])),
        })

    qhf_records = [item for item in records if item["module"] == QHF_MODULE]
    by_state = {
        state: _summary(item for item in qhf_records if item["state"] == state)
        for state in ("FAILED_AUCTION", "CONTINUATION", "UNRESOLVED")
    }
    interval_summary = {
        "interval": interval,
        "start": spec["start"],
        "end_exclusive": spec["end_exclusive"],
        "role": spec.get("role"),
        "all_plans": len(records),
        "qhf_plans": len(qhf_records),
        "qhf_summary": _summary(qhf_records),
        "by_state": by_state,
        "state_counts": dict(Counter(item["state"] for item in qhf_records)),
    }
    return interval_summary, records, manifests


def _markdown(payload: dict[str, Any]) -> str:
    aggregate = payload["aggregate"]
    lines = [
        "# Candidate 13 V12 spot/perpetual causal-state diagnostic",
        "",
        "## Status",
        "",
        "Development-only diagnostic on already exposed E01-E06 intervals. It is not holdout evidence and cannot support a success claim.",
        "",
        "The policy was frozen before outcomes were joined:",
        "",
        "- `FAILED_AUCTION`: the attempted move was perpetual-led in both price and aggressor flow, expanded basis in the attempted direction, then basis, spot price, spot flow and perpetual price all confirmed the reversal before entry.",
        "- `CONTINUATION`: spot price and aggressor flow backed the attempted move and spot still continued it while basis did not revert.",
        "- `UNRESOLVED`: neither mutually exclusive causal contract was complete.",
        "",
        "Quarter-hour common flow remains context only. The spot/perpetual relation is the independent pre-entry state variable.",
        "",
        "## Aggregate QHF outcomes by pre-entry state",
        "",
        "| state | plans | filled | unique filled episodes | wins | losses | win rate | realized PnL | payoff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for state in ("FAILED_AUCTION", "CONTINUATION", "UNRESOLVED"):
        item = aggregate["by_state"][state]
        payoff = "n/a" if item["payoff_ratio"] is None else f"{item['payoff_ratio']:.3f}"
        lines.append(
            f"| {state} | {item['plans']} | {item['filled']} | {item['filled_unique_episodes']} | "
            f"{item['wins']} | {item['losses']} | {item['win_rate']:.2%} | "
            f"{item['realized_pnl']:.2f} | {payoff} |",
        )
    lines.extend([
        "",
        "## Interval detail",
        "",
        "| interval | QHF plans | FAILED_AUCTION filled / WR / PnL | CONTINUATION filled / WR / PnL | UNRESOLVED filled / WR / PnL |",
        "|---|---:|---:|---:|---:|",
    ])
    for item in payload["intervals"]:
        cells = []
        for state in ("FAILED_AUCTION", "CONTINUATION", "UNRESOLVED"):
            state_item = item["by_state"][state]
            cells.append(
                f"{state_item['filled']} / {state_item['win_rate']:.1%} / {state_item['realized_pnl']:.2f}",
            )
        lines.append(
            f"| {item['interval']} | {item['qhf_plans']} | {cells[0]} | {cells[1]} | {cells[2]} |",
        )
    lines.extend([
        "",
        "## Decision rule",
        "",
        "This diagnostic does not tune magnitude thresholds. It tests only causal sign and ordering relations. A V12 trading implementation is justified only if the predeclared `FAILED_AUCTION` route is materially stronger across multiple exposed intervals without relying on one episode.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    protocol = _load_json(args.protocol)
    holdouts = protocol["selection"]["holdouts"]
    interval_summaries: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    for interval, spec in holdouts.items():
        summary, interval_records, interval_manifests = analyze_interval(
            interval=interval,
            spec=spec,
            results_root=args.results_root,
            data_dir=args.data_dir,
        )
        interval_summaries.append(summary)
        records.extend(interval_records)
        manifests.extend(interval_manifests)

    qhf_records = [item for item in records if item["module"] == QHF_MODULE]
    aggregate = {
        "qhf_summary": _summary(qhf_records),
        "by_state": {
            state: _summary(item for item in qhf_records if item["state"] == state)
            for state in ("FAILED_AUCTION", "CONTINUATION", "UNRESOLVED")
        },
        "state_counts": dict(Counter(item["state"] for item in qhf_records)),
        "active_intervals_by_state": {
            state: sum(
                item["by_state"][state]["filled"] > 0
                for item in interval_summaries
            )
            for state in ("FAILED_AUCTION", "CONTINUATION", "UNRESOLVED")
        },
    }
    payload = {
        "schema": "candidate-13-v12-spot-perp-causal-state-diagnostic-v1",
        "created_utc": datetime.now(UTC).isoformat(),
        "development_only": True,
        "source_protocol": str(args.protocol),
        "policy_lock": {
            "threshold_tuning": False,
            "time_visibility": "completed one-minute spot and perpetual bars only",
            "failed_auction": [
                "attempt_directional_in_perp",
                "attempt_perp_led_price",
                "attempt_perp_led_flow",
                "attempt_basis_expanded",
                "basis_reverted_toward_reversal",
                "spot_confirmed_reversal_price",
                "spot_confirmed_reversal_flow",
                "perp_confirmed_reversal_price",
            ],
            "continuation": [
                "spot_backed_attempt_price",
                "spot_backed_attempt_flow",
                "spot_still_continued_attempt",
                "basis_reverted_toward_reversal == false",
            ],
        },
        "aggregate": aggregate,
        "intervals": interval_summaries,
        "records": records,
        "source_manifest": manifests,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(aggregate, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
