#!/usr/bin/env python3
"""Apply and verify only the pre-data implementation fixes for locked v95."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "v95_mature_swing_breakout_core.py"
ORIGINAL_SHA = "cbb08f4ed11b02638c30a898444e1d8bc8a1867cfc61036044905866524295b0"
FIX01_SHA = "d519683c3e3c5df6351cf003ca30a5ab5fa21053622f4434fd6422813a851663"
FINAL_SHA = "60ea90e83b3f1cffb0d816c0cb08ff6c6bf6b160a025c7da5f75d7d83e5598a8"
FINAL_PAYLOAD_SHA = "e5847fdb56138b4b09ddb3bf62d468d3e96cb6011957121169c5c121878f2356"
FIXED_WEEK = {
    "symbol": "BTCUSDT",
    "start_utc": "2025-10-06T00:00:00Z",
    "end_utc": "2025-10-13T00:00:00Z",
    "selection_seed": 2026080695,
}


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def replace_once(text: str, before: str, after: str) -> str:
    count = text.count(before)
    if count != 1:
        raise AssertionError(f"expected one source occurrence, found {count}: {before[:100]!r}")
    return text.replace(before, after)


def apply_fix01(text: str) -> str:
    replacements = {
        "if not 1 <= self.minimum_outside_closes <= self.classification_minutes + 1:":
            "if not 1 <= self.minimum_outside_closes <= self.classification_minutes:",
        "confirm_end = min(position + config.defense_confirmation_minutes, right - 1)":
            "confirm_end = min(position + config.defense_confirmation_minutes - 1, right - 1)",
        "classification_end = min(event_position + config.classification_minutes, len(x) - 1)":
            "classification_end = min(event_position + config.classification_minutes - 1, len(x) - 1)",
    }
    for before, after in replacements.items():
        text = replace_once(text, before, after)
    ast.parse(text)
    if sha_text(text) != FIX01_SHA:
        raise AssertionError("unexpected fix01 source hash")
    return text


def apply_fix02(text: str) -> str:
    replacements = [
        ('return bars.loc[bars["count"] >= minutes - 1, ["open", "high", "low", "close"]]',
         'return bars.loc[bars["count"] == minutes, ["open", "high", "low", "close"]]'),
        ('earliest = int(np.searchsorted(index_ns, earliest_ns, side="left"))',
         'earliest = int(np.searchsorted(index_ns, earliest_ns, side="right"))'),
        ('    closes = bars["close"].to_numpy(dtype=float)\n', ''),
        ('        later_closes = closes[confirmation + 1 :]\n',
         '        later_highs = highs[confirmation + 1 :]\n        later_lows = lows[confirmation + 1 :]\n'),
        ('        if is_high and not np.any(later_closes > high):\n            intact_highs.append(high)\n        if is_low and not np.any(later_closes < low):\n            intact_lows.append(low)\n',
         '        if is_high and not np.any(later_highs > high):\n            intact_highs.append(high)\n        if is_low and not np.any(later_lows < low):\n            intact_lows.append(low)\n'),
        ('    end_position = min(event_position + config.displacement_minutes, len(x) - 1)',
         '    end_position = min(event_position + config.displacement_minutes - 1, len(x) - 1)'),
        ('    used_observed_times: set[int] = set()\n    cooldown_until = -1\n', ''),
        ('            event_position=event_position,\n',
         '            event_position=classification_end,\n'),
        ('            if observed_ns <= cooldown_until or observed_ns in used_observed_times:\n                continue\n', ''),
        ('            selected = _select_nearest_target(\n                levels=pivot_highs if side == "BUY" else pivot_lows,\n',
         '            path = x.iloc[event_position : position + 1]\n            path_extreme = (\n                float(path["raw_high"].max()) if side == "BUY" else float(path["raw_low"].min())\n            )\n            intact_at_entry = (\n                [level for level in pivot_highs if level > path_extreme]\n                if side == "BUY"\n                else [level for level in pivot_lows if level < path_extreme]\n            )\n            selected = _select_nearest_target(\n                levels=intact_at_entry,\n'),
        ('                "selected_nearest_intact_swing": target,\n',
         '                "entry_path_extreme": path_extreme,\n                "selected_nearest_intact_swing": target,\n'),
        ('            used_observed_times.add(observed_ns)\n            cooldown_until = observed_ns + config.cooldown_minutes * NS_MINUTE\n', ''),
        ('    unique: list[RotationSignal] = []\n    seen: set[int] = set()\n    for signal in signals:\n        if signal.observed_time_ns in seen:\n            continue\n        seen.add(signal.observed_time_ns)\n        if signal.source_max_market_time_ns > signal.observed_time_ns:\n            raise AssertionError("future information detected in v95")\n        unique.append(signal)\n',
         '    unique: list[RotationSignal] = []\n    seen: set[int] = set()\n    cooldown_until = -1\n    for signal in signals:\n        if signal.observed_time_ns in seen or signal.observed_time_ns <= cooldown_until:\n            continue\n        seen.add(signal.observed_time_ns)\n        if signal.source_max_market_time_ns > signal.observed_time_ns:\n            raise AssertionError("future information detected in v95")\n        unique.append(signal)\n        cooldown_until = signal.observed_time_ns + config.cooldown_minutes * NS_MINUTE\n'),
    ]
    for before, after in replacements:
        text = replace_once(text, before, after)
    ast.parse(text)
    if sha_text(text) != FINAL_SHA:
        raise AssertionError("unexpected final source hash")
    return text


def write_records() -> None:
    common = {
        "candidate": "candidate-02-v95-mature-defended-swing-common-accepted-breakout",
        "data_status_before_fix": "NO_V95_RAW_OR_DERIVED_MARKET_DATA_COLLECTED",
        "locked_week_unchanged": FIXED_WEEK,
        "risk_and_cost_model_unchanged": True,
        "performance_engine_unchanged": "NautilusTrader 1.230.0",
        "custom_backtest_engine": False,
        "precommitted_logic_ablation_unchanged": "require_defense_memory true to false",
    }
    fix01 = common | {
        "classification": "IMPLEMENTATION_ERROR_FIXED_BEFORE_FIRST_WEEK_DATA_COLLECTION",
        "strategy_logic_and_parameters_unchanged": True,
        "controlled_change": {"old_core_sha256": ORIGINAL_SHA, "new_core_sha256": FIX01_SHA, "source_line_deltas": 3},
        "implementation_error": {
            "cause": "inclusive dataframe endpoints made a configured N-completed-minute window evaluate N+1 bars",
            "affected_windows": [
                {"name": "defense_confirmation_minutes", "before": "six bars", "after": "five completed bars"},
                {"name": "classification_minutes", "before": "four bars", "after": "three completed bars"},
                {"name": "minimum_outside_closes validation", "before": "allowed N+1", "after": "allows at most N"},
            ],
        },
    }
    fix02 = common | {
        "classification": "IMPLEMENTATION_ERRORS_FIXED_BEFORE_FIRST_WEEK_DATA_COLLECTION",
        "strategy_hypothesis_and_parameters_unchanged": True,
        "controlled_change": {"old_core_sha256": FIX01_SHA, "new_core_sha256": FINAL_SHA, "new_combined_payload_sha256": FINAL_PAYLOAD_SHA},
        "implementation_errors": [
            {"name": "acceptance_to_displacement_future_leak", "fix": "start displacement at the completed classification close"},
            {"name": "event_order_future_cooldown", "fix": "apply cooldown after sorting candidates by observed time"},
            {"name": "displacement_window_inclusive_endpoint", "fix": "evaluate exactly six completed candles"},
            {"name": "incomplete_higher_timeframe_bar_admission", "fix": "require all fifteen one-minute observations"},
            {"name": "target_not_intact_at_entry", "fix": "invalidate on wick traversal and current event-to-entry path traversal"},
            {"name": "maturity_boundary_gap", "fix": "include the exact minimum-age candle in survival"},
        ],
    }
    (ROOT / "v95_implementation_fix_01.json").write_text(json.dumps(fix01, indent=2, sort_keys=True) + "\n")
    (ROOT / "v95_implementation_fix_02.json").write_text(json.dumps(fix02, indent=2, sort_keys=True) + "\n")


def verify_payload() -> None:
    files = [
        "v95_mature_swing_breakout_core.py",
        "v95_nt_backtest.py",
        "v95_base_config.json",
        "v95_mature_swing_breakout_lock.json",
        "v95_design.md",
    ]
    combined = hashlib.sha256(b"".join((ROOT / name).read_bytes() for name in files)).hexdigest()
    if combined != FINAL_PAYLOAD_SHA:
        raise AssertionError(f"unexpected combined payload hash {combined}")
    (ROOT / "v95_payload_sha256.txt").write_text(combined)
    lock = json.loads((ROOT / "v95_mature_swing_breakout_lock.json").read_text())
    config = json.loads((ROOT / "v95_base_config.json").read_text())
    assert lock["first_week"] == FIXED_WEEK | {"raw_data_status_at_lock": "NOT_COLLECTED_FOR_V95"}
    assert lock["performance_engine"] == "NautilusTrader 1.230.0"
    assert lock["custom_backtest_engine"] is False
    assert float(lock["risk_fraction"]) == 0.03
    assert lock["global_pending_entry_plus_position_limit"] == 1
    assert config["scenario"]["require_defense_memory"] is True
    assert config["risk"]["maximum_notional_cap"] is None
    assert config["risk"]["score_risk_multiplier"] is None


def run_synthetic_tests() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import v95_mature_swing_breakout_core as v95

    index = pd.date_range("2025-01-01 00:01", periods=15, freq="min", tz="UTC")
    raw = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}, index=index)
    assert len(v95._completed_bars(raw, start=index[0] - pd.Timedelta(minutes=1), end=index[-1], minutes=15)) == 1
    assert v95._completed_bars(raw.drop(index[7]), start=index[0] - pd.Timedelta(minutes=1), end=index[-1], minutes=15).empty

    bidx = pd.date_range("2025-01-01 00:15", periods=7, freq="15min", tz="UTC")
    bars = pd.DataFrame({"open": [8,9,10,9,8,9,8], "high": [9,10,12,10,9,13,9], "low": [7,8,9,8,7,8,7], "close": [8,9,11,9,8,8.5,8]}, index=bidx, dtype=float)
    highs, _ = v95._intact_pivots(bars, 2)
    assert 12.0 not in highs

    start = 5
    xidx = pd.date_range("2025-01-01", periods=20, freq="min", tz="UTC")
    frame = pd.DataFrame({"raw_open": np.full(20,100.0), "raw_high": np.full(20,100.1), "raw_low": np.full(20,99.9), "raw_close": np.full(20,100.0), "body": np.zeros(20), "body_threshold": np.full(20,0.1), "atr": np.ones(20)}, index=xidx)
    seventh = start + 6
    frame.loc[xidx[seventh], ["raw_open","raw_high","raw_low","raw_close","body"]] = [100,101.1,100.6,101,1]
    frame.loc[xidx[seventh-2], "raw_high"] = 100.2
    cfg = v95.MatureSwingBreakoutConfig()
    assert v95._find_displacement(x=frame, event_position=start, boundary=100.0, direction=1, config=cfg) is None
    frame.loc[xidx[seventh], ["raw_open","raw_high","raw_low","raw_close","body"]] = [100,100.1,99.9,100,0]
    sixth = start + 5
    frame.loc[xidx[sixth], ["raw_open","raw_high","raw_low","raw_close","body"]] = [100,101.1,100.6,101,1]
    frame.loc[xidx[sixth-2], "raw_high"] = 100.2
    found = v95._find_displacement(x=frame, event_position=start, boundary=100.0, direction=1, config=cfg)
    assert found is not None and found[0] == sixth

    confirmation = pd.Timestamp("2025-01-01 00:00", tz="UTC")
    midx = pd.date_range(confirmation + pd.Timedelta(minutes=1), periods=600, freq="min", tz="UTC")
    maturity_raw = pd.DataFrame({"open":100.0,"high":100.5,"low":99.5,"close":100.0}, index=midx)
    maturity_raw.loc[confirmation + pd.Timedelta(minutes=480), "high"] = 101.1
    candidate = v95.SwingLevelCandidate("test-high", "HIGH", 101.0, int((confirmation-pd.Timedelta(minutes=30)).value), int(confirmation.value), int((confirmation+pd.Timedelta(minutes=600)).value))
    maturity_cfg = v95.MatureSwingBreakoutConfig(minimum_level_age_minutes=480, maximum_level_age_minutes=600, require_defense_memory=False)
    assert v95._qualify_levels(maturity_raw, candidates=[candidate], atr=pd.Series(1.0,index=midx), config=maturity_cfg) == []

    source = CORE.read_text()
    assert "event_position=classification_end" in source
    assert "path = x.iloc[event_position : position + 1]" in source
    assert "observed_ns <= cooldown_until or observed_ns in used_observed_times" not in source
    sort_at = source.index("signals.sort(")
    assert sort_at < source.index("cooldown_until = -1", sort_at)

    verification = {
        "candidate": "candidate-02-v95-mature-defended-swing-common-accepted-breakout",
        "data_status": "NO_V95_MARKET_DATA_TOUCHED_BY_THIS_VERIFICATION",
        "tests": {
            "complete_higher_timeframe_bars": "PASS",
            "wick_based_target_intactness": "PASS",
            "exact_displacement_horizon": "PASS",
            "maturity_boundary_survival": "PASS",
            "causal_state_order_and_chronological_cooldown": "PASS",
        },
        "core_sha256": FINAL_SHA,
        "combined_payload_sha256": FINAL_PAYLOAD_SHA,
    }
    (ROOT / "v95_predata_verification.json").write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n")


def main() -> None:
    text = CORE.read_text(encoding="utf-8")
    current = sha_text(text)
    if current == ORIGINAL_SHA:
        text = apply_fix01(text)
        current = FIX01_SHA
    if current == FIX01_SHA:
        text = apply_fix02(text)
        CORE.write_text(text, encoding="utf-8")
        current = FINAL_SHA
    if current != FINAL_SHA:
        raise AssertionError(f"unrecognized v95 source hash {current}")
    ast.parse(CORE.read_text())
    write_records()
    verify_payload()
    run_synthetic_tests()
    print(json.dumps({"status": "PASS", "core_sha256": FINAL_SHA, "payload_sha256": FINAL_PAYLOAD_SHA}, sort_keys=True))


if __name__ == "__main__":
    main()
