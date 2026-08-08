#!/usr/bin/env python3
"""Causal conditional-response diagnostic for Candidate 15 V12.

This is not a backtester and never constructs a synthetic account. It asks a
single mechanism question on synchronized Binance USD-M one-minute bars:

    after an unusually large same-sign move in the other three allowed markets,
    can prior-only cross-predictive response estimates identify the sign and
    size of the focal market's next 1/3/5/10 minute return?

Every fitted response pair is mature before it can enter a model. The current
shock, its future focal return, and evaluation-period outcomes are never used to
fit the prediction emitted for that shock. A later NautilusTrader strategy may
reuse only a mechanism that survives this diagnostic.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import importlib.util
import json
from math import isfinite, log
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CANDIDATE14 = REPO / "research" / "candidate-14"
CANDIDATE15 = REPO / "research" / "candidate-15"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
HORIZONS = (1, 3, 5, 10)
MODEL_EVENT_WINDOWS = (16, 32, 48)
MIN_MODEL_EVENTS = 16
ROLLING_MINUTES = 1440
PATH_MINUTES = 5
ROUND_TRIP_MAKER_RATE = 0.0008  # frozen 4 bp maker each side; diagnostic only


def _load_candidate14_runner() -> Any:
    path = CANDIDATE14 / "run_leadership_scdam_base.py"
    spec = importlib.util.spec_from_file_location("candidate14_base_for_v12", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(CANDIDATE14))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(CANDIDATE14))
        except ValueError:
            pass
    return module


def _sign(value: float) -> int:
    return 1 if value > 0.0 else -1 if value < 0.0 else 0


def _zero_intercept_beta(rows: Iterable[tuple[float, float]]) -> float | None:
    data = list(rows)
    if len(data) < MIN_MODEL_EVENTS:
        return None
    denominator = sum(x * x for x, _ in data)
    if not isfinite(denominator) or denominator <= 0.0:
        return None
    numerator = sum(x * y for x, y in data)
    beta = numerator / denominator
    return beta if isfinite(beta) else None


def _quantile(values: deque[float], probability: float) -> float | None:
    if len(values) < 120:
        return None
    result = float(np.quantile(np.asarray(values, dtype="float64"), probability))
    return result if isfinite(result) else None


@dataclass(frozen=True, slots=True)
class PendingResponse:
    receiver: str
    regime: str
    source_ts_ns: int
    horizon: int
    factor: float
    source_close: float


@dataclass(frozen=True, slots=True)
class Forecast:
    interval: str
    ts_ns: int
    receiver: str
    regime: str
    factor: float
    factor_abs_quantile: float
    path_efficiency: float
    path_concentration: float
    direction: int
    predicted_return_10m: float
    predicted_return_by_horizon: dict[int, float]
    beta_by_horizon_and_window: dict[int, dict[int, float]]
    focal_signed_flow: float
    focal_event_return: float
    response_10m: float
    directional_response_10m: float
    directional_response_after_two_maker_fees: float
    mfe_10m: float
    mae_10m: float


class OnlineConditionalResponse:
    def __init__(self) -> None:
        self.histories: dict[tuple[str, str, int], deque[tuple[float, float]]] = {
            (symbol, regime, horizon): deque(maxlen=max(MODEL_EVENT_WINDOWS))
            for symbol in SYMBOLS
            for regime in ("DIFFUSE", "CONCENTRATED")
            for horizon in HORIZONS
        }
        self.pending_by_maturity: dict[int, list[PendingResponse]] = defaultdict(list)

    def mature(self, ts_ns: int, closes: dict[str, float]) -> None:
        for item in self.pending_by_maturity.pop(ts_ns, []):
            current = closes.get(item.receiver)
            if current is None or current <= 0.0 or item.source_close <= 0.0:
                continue
            response = log(current / item.source_close)
            if not isfinite(response):
                continue
            self.histories[(item.receiver, item.regime, item.horizon)].append(
                (item.factor, response),
            )

    def schedule(
        self,
        *,
        receiver: str,
        regime: str,
        ts_ns: int,
        factor: float,
        source_close: float,
    ) -> None:
        minute_ns = 60_000_000_000
        for horizon in HORIZONS:
            self.pending_by_maturity[ts_ns + horizon * minute_ns].append(
                PendingResponse(
                    receiver=receiver,
                    regime=regime,
                    source_ts_ns=ts_ns,
                    horizon=horizon,
                    factor=factor,
                    source_close=source_close,
                ),
            )

    def predict(
        self,
        *,
        receiver: str,
        regime: str,
        factor: float,
    ) -> tuple[int, dict[int, float], dict[int, dict[int, float]]] | None:
        predictions: dict[int, float] = {}
        beta_grid: dict[int, dict[int, float]] = {}
        signs: list[int] = []
        for horizon in HORIZONS:
            history = self.histories[(receiver, regime, horizon)]
            horizon_betas: dict[int, float] = {}
            for window in MODEL_EVENT_WINDOWS:
                if len(history) < window:
                    return None
                beta = _zero_intercept_beta(list(history)[-window:])
                if beta is None:
                    return None
                horizon_betas[window] = beta
                signs.append(_sign(beta * factor))
            beta_grid[horizon] = horizon_betas
            predictions[horizon] = float(
                np.median([beta * factor for beta in horizon_betas.values()]),
            )
        if not signs or 0 in signs or len(set(signs)) != 1:
            return None
        direction = signs[0]
        directional = {h: direction * value for h, value in predictions.items()}
        if min(directional.values()) <= 0.0 or directional[10] < directional[1]:
            return None
        return direction, predictions, beta_grid


def _load_interval_frames(interval: str, data_dir: Path) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    protocol = json.loads((CANDIDATE15 / "protocol-v11.json").read_text(encoding="utf-8"))
    selected = protocol["selection"]["intervals"][interval]
    start = date.fromisoformat(selected["start"])
    end_exclusive = date.fromisoformat(selected["end_exclusive"])
    warmup_start = start - timedelta(days=max(3, int(protocol["selection"]["warmup_days"])))
    runner = _load_candidate14_runner()
    frames: dict[str, pd.DataFrame] = {}
    manifests: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame, manifest = runner.load_symbol_bars(
            symbol,
            warmup_start,
            end_exclusive - timedelta(days=1),
            data_dir,
        )
        frames[symbol] = frame
        manifests.extend(manifest)
    metadata = {
        "interval": interval,
        "start": start.isoformat(),
        "end_exclusive": end_exclusive.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "manifest_files": len(manifests),
        "manifest_sha256": sorted(item["sha256"] for item in manifests),
    }
    return frames, metadata


def _aligned_arrays(frames: dict[str, pd.DataFrame]) -> tuple[pd.DatetimeIndex, dict[str, dict[str, np.ndarray]]]:
    common = None
    for frame in frames.values():
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None or common.empty:
        raise RuntimeError("no synchronized bars")
    common = common.sort_values()
    arrays: dict[str, dict[str, np.ndarray]] = {}
    for symbol, frame in frames.items():
        view = frame.loc[common]
        close = view["close"].to_numpy(dtype="float64")
        open_ = view["open"].to_numpy(dtype="float64")
        high = view["high"].to_numpy(dtype="float64")
        low = view["low"].to_numpy(dtype="float64")
        volume = view["volume"].to_numpy(dtype="float64")
        taker = view["taker_buy_volume"].to_numpy(dtype="float64")
        returns = np.zeros_like(close)
        returns[1:] = np.log(close[1:] / close[:-1])
        signed_flow = np.divide(
            2.0 * taker,
            volume,
            out=np.zeros_like(taker),
            where=volume > 0.0,
        ) - np.where(volume > 0.0, 1.0, 0.0)
        signed_flow = np.clip(signed_flow, -1.0, 1.0)
        arrays[symbol] = {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "return": returns,
            "signed_flow": signed_flow,
        }
    return common, arrays


def _path_metrics(values: deque[float]) -> tuple[float, float] | None:
    if len(values) < PATH_MINUTES:
        return None
    recent = np.asarray(list(values)[-PATH_MINUTES:], dtype="float64")
    absolute = np.abs(recent)
    total = float(absolute.sum())
    if not isfinite(total) or total <= 0.0:
        return None
    efficiency = abs(float(recent.sum())) / total
    concentration = float(absolute.max()) / total
    if not isfinite(efficiency) or not isfinite(concentration):
        return None
    return efficiency, concentration


def _future_geometry(
    arrays: dict[str, dict[str, np.ndarray]],
    symbol: str,
    index: int,
    direction: int,
) -> tuple[float, float, float] | None:
    end = index + 10
    if end >= len(arrays[symbol]["close"]):
        return None
    close = arrays[symbol]["close"]
    high = arrays[symbol]["high"]
    low = arrays[symbol]["low"]
    source = float(close[index])
    if source <= 0.0:
        return None
    response = log(float(close[end]) / source)
    if direction > 0:
        mfe = log(float(np.max(high[index + 1 : end + 1])) / source)
        mae = log(float(np.min(low[index + 1 : end + 1])) / source)
    else:
        mfe = log(source / float(np.min(low[index + 1 : end + 1])))
        mae = log(source / float(np.max(high[index + 1 : end + 1])))
    return response, mfe, mae


def diagnose(interval: str, output: Path) -> dict[str, Any]:
    frames, metadata = _load_interval_frames(interval, output / "data")
    index, arrays = _aligned_arrays(frames)
    evaluation_start = pd.Timestamp(metadata["start"], tz="UTC")
    evaluation_end = pd.Timestamp(metadata["end_exclusive"], tz="UTC")

    factor_histories = {symbol: deque(maxlen=ROLLING_MINUTES) for symbol in SYMBOLS}
    path_eff_histories = {symbol: deque(maxlen=ROLLING_MINUTES) for symbol in SYMBOLS}
    path_conc_histories = {symbol: deque(maxlen=ROLLING_MINUTES) for symbol in SYMBOLS}
    current_path = {symbol: deque(maxlen=PATH_MINUTES) for symbol in SYMBOLS}
    prior_above = {symbol: False for symbol in SYMBOLS}
    model = OnlineConditionalResponse()
    forecasts: list[Forecast] = []
    raw_events: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    counts: Counter[str] = Counter()

    for i, timestamp in enumerate(index):
        ts_ns = int(timestamp.value)
        closes = {symbol: float(arrays[symbol]["close"][i]) for symbol in SYMBOLS}
        model.mature(ts_ns, closes)
        if i == 0:
            continue

        for receiver in SYMBOLS:
            peers = [symbol for symbol in SYMBOLS if symbol != receiver]
            peer_returns = [float(arrays[symbol]["return"][i]) for symbol in peers]
            factor = float(np.median(peer_returns))
            current_path[receiver].append(factor)
            path = _path_metrics(current_path[receiver])
            shock_threshold = _quantile(factor_histories[receiver], 0.95)
            efficiency_median = _quantile(path_eff_histories[receiver], 0.50)
            concentration_q25 = _quantile(path_conc_histories[receiver], 0.25)
            concentration_q75 = _quantile(path_conc_histories[receiver], 0.75)

            all_same_sign = all(value > 0.0 for value in peer_returns) or all(
                value < 0.0 for value in peer_returns
            )
            above = bool(
                shock_threshold is not None
                and all_same_sign
                and abs(factor) >= shock_threshold
                and factor != 0.0
            )
            first_passage = above and not prior_above[receiver]
            prior_above[receiver] = above

            if first_passage:
                if path is None or None in (
                    efficiency_median,
                    concentration_q25,
                    concentration_q75,
                ):
                    skips["PATH_REGIME_HISTORY_INCOMPLETE"] += 1
                else:
                    efficiency, concentration = path
                    regime = "UNRESOLVED"
                    if efficiency >= float(efficiency_median) and concentration <= float(concentration_q25):
                        regime = "DIFFUSE"
                    elif concentration >= float(concentration_q75):
                        regime = "CONCENTRATED"
                    if regime == "UNRESOLVED":
                        skips["PATH_REGIME_UNRESOLVED"] += 1
                    else:
                        counts[f"event::{receiver}::{regime}"] += 1
                        event = {
                            "interval": interval,
                            "ts_ns": ts_ns,
                            "timestamp": timestamp.isoformat(),
                            "receiver": receiver,
                            "peers": peers,
                            "peer_returns": peer_returns,
                            "factor": factor,
                            "shock_threshold": shock_threshold,
                            "path_efficiency": efficiency,
                            "path_concentration": concentration,
                            "regime": regime,
                            "in_evaluation": bool(evaluation_start <= timestamp < evaluation_end),
                        }
                        raw_events.append(event)
                        prediction = model.predict(
                            receiver=receiver,
                            regime=regime,
                            factor=factor,
                        )
                        model.schedule(
                            receiver=receiver,
                            regime=regime,
                            ts_ns=ts_ns,
                            factor=factor,
                            source_close=closes[receiver],
                        )
                        if prediction is None:
                            skips["PRIOR_ONLY_RESPONSE_CONSENSUS_ABSENT"] += 1
                        elif evaluation_start <= timestamp < evaluation_end:
                            direction, predicted, beta_grid = prediction
                            geometry = _future_geometry(arrays, receiver, i, direction)
                            if geometry is None:
                                skips["EVALUATION_RESPONSE_PATH_INCOMPLETE"] += 1
                            else:
                                response_10m, mfe, mae = geometry
                                directional_response = direction * response_10m
                                forecasts.append(
                                    Forecast(
                                        interval=interval,
                                        ts_ns=ts_ns,
                                        receiver=receiver,
                                        regime=regime,
                                        factor=factor,
                                        factor_abs_quantile=float(shock_threshold),
                                        path_efficiency=efficiency,
                                        path_concentration=concentration,
                                        direction=direction,
                                        predicted_return_10m=float(predicted[10]),
                                        predicted_return_by_horizon=dict(predicted),
                                        beta_by_horizon_and_window=beta_grid,
                                        focal_signed_flow=float(arrays[receiver]["signed_flow"][i]),
                                        focal_event_return=float(arrays[receiver]["return"][i]),
                                        response_10m=response_10m,
                                        directional_response_10m=directional_response,
                                        directional_response_after_two_maker_fees=(
                                            directional_response - ROUND_TRIP_MAKER_RATE
                                        ),
                                        mfe_10m=mfe,
                                        mae_10m=mae,
                                    ),
                                )
            factor_histories[receiver].append(abs(factor))
            if path is not None:
                path_eff_histories[receiver].append(path[0])
                path_conc_histories[receiver].append(path[1])

    forecast_rows = [asdict(item) for item in forecasts]
    for row in forecast_rows:
        row["predicted_return_by_horizon"] = {
            str(key): value for key, value in row["predicted_return_by_horizon"].items()
        }
        row["beta_by_horizon_and_window"] = {
            str(h): {str(w): beta for w, beta in windows.items()}
            for h, windows in row["beta_by_horizon_and_window"].items()
        }

    def _summary(rows: list[Forecast]) -> dict[str, Any]:
        if not rows:
            return {
                "forecasts": 0,
                "directional_hit_rate": 0.0,
                "positive_after_two_maker_fees_rate": 0.0,
                "mean_directional_response_10m_bps": 0.0,
                "median_directional_response_10m_bps": 0.0,
                "mean_after_two_maker_fees_bps": 0.0,
                "median_mfe_10m_bps": 0.0,
                "median_mae_10m_bps": 0.0,
            }
        directional = np.asarray([item.directional_response_10m for item in rows])
        after_cost = np.asarray(
            [item.directional_response_after_two_maker_fees for item in rows],
        )
        mfe = np.asarray([item.mfe_10m for item in rows])
        mae = np.asarray([item.mae_10m for item in rows])
        return {
            "forecasts": len(rows),
            "directional_hit_rate": float(np.mean(directional > 0.0)),
            "positive_after_two_maker_fees_rate": float(np.mean(after_cost > 0.0)),
            "mean_directional_response_10m_bps": float(np.mean(directional) * 10000.0),
            "median_directional_response_10m_bps": float(np.median(directional) * 10000.0),
            "mean_after_two_maker_fees_bps": float(np.mean(after_cost) * 10000.0),
            "median_mfe_10m_bps": float(np.median(mfe) * 10000.0),
            "median_mae_10m_bps": float(np.median(mae) * 10000.0),
        }

    by_regime = {
        regime: _summary([item for item in forecasts if item.regime == regime])
        for regime in ("DIFFUSE", "CONCENTRATED")
    }
    by_symbol = {
        symbol: _summary([item for item in forecasts if item.receiver == symbol])
        for symbol in SYMBOLS
    }
    by_direction_model = {
        "SPILLOVER_POSITIVE_BETA": _summary(
            [item for item in forecasts if _sign(item.factor) == item.direction],
        ),
        "SEESAW_NEGATIVE_BETA": _summary(
            [item for item in forecasts if _sign(item.factor) == -item.direction],
        ),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "events.json").write_text(
        json.dumps(raw_events, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output / "forecasts.json").write_text(
        json.dumps(forecast_rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    result = {
        "schema": "candidate-15-v12-causal-cross-predictive-diagnostic-v1",
        "diagnostic_only_not_account_backtest": True,
        "interval": interval,
        "metadata": metadata,
        "event_counts": dict(counts),
        "skip_counts": dict(skips),
        "overall": _summary(forecasts),
        "by_regime": by_regime,
        "by_symbol": by_symbol,
        "by_direction_model": by_direction_model,
        "forecast_rows": len(forecasts),
        "model_contract": {
            "peer_factor": "MEDIAN_CURRENT_ONE_MINUTE_RETURN_OF_OTHER_THREE",
            "event": "ALL_THREE_PEERS_SAME_SIGN_AND_PRIOR_24H_95P_FIRST_PASSAGE",
            "path_regime": (
                "DIFFUSE if 5m efficiency >= prior median and concentration <= prior Q25; "
                "CONCENTRATED if concentration >= prior Q75; otherwise no forecast"
            ),
            "response_horizons_minutes": list(HORIZONS),
            "response_model": "ZERO_INTERCEPT_BETA_ON_PRIOR_MATURE_EVENT_RESPONSES",
            "nested_event_windows": list(MODEL_EVENT_WINDOWS),
            "minimum_events": MIN_MODEL_EVENTS,
            "sign_consensus": "ALL_HORIZONS_AND_NESTED_WINDOWS",
            "causal_cutoff": "EVERY_RESPONSE_MATURE_BEFORE_MODEL_USE",
            "diagnostic_cost": "TWO_FROZEN_MAKER_FEES_ONLY; NOT A TRADE PNL CLAIM",
        },
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("interval", choices=("E01", "E02", "E03", "E04", "E05", "E06"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(args.interval, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
