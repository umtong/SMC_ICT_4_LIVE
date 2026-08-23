from __future__ import annotations

import csv
from pathlib import Path
from typing import Any
import zipfile

import pytest

from research.campaign_policy_fast_replay import (
    FastReplayError,
    discover_binance_vision_5m,
    iter_synchronized_frames,
    run_fast_replay,
    write_replay_result,
)
from smc_ict_4.episode_policy_live.domain import Bar
from smc_ict_4.episode_policy_live.neutral_policy import (
    ExecutionFeedback,
    MARKET_SYMBOLS,
    MarketFrame,
    OrderIntent,
    PolicyOutput,
)


STEP_NS = 300_000_000_000


def _frame(index: int, *, btc=(100.0, 100.5, 99.5, 100.0)) -> MarketFrame:
    bars = []
    for symbol in MARKET_SYMBOLS:
        prices = btc if symbol == "BTCUSDT" else (100.0, 100.5, 99.5, 100.0)
        bars.append(
            Bar(
                symbol=symbol,
                interval_minutes=5,
                open_time_ns=index * STEP_NS,
                close_time_ns=(index + 1) * STEP_NS - 1_000_000,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                volume=1.0,
                quote_volume=100.0,
                taker_buy_quote_volume=50.0,
                trade_count=1,
            )
        )
    return MarketFrame(tuple(bars))


class _Policy:
    def __init__(self, actions: dict[int, PolicyOutput]) -> None:
        self.actions = actions
        self.index = 0
        self.feedback: list[ExecutionFeedback] = []
        self._opportunities: list[dict[str, Any]] = []

    def on_market_frame(self, frame: MarketFrame) -> PolicyOutput:
        output = self.actions.get(self.index, PolicyOutput())
        self._opportunities = [
            {
                "decision_time_ns": frame.close_time_ns,
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "source_id": f"source-{self.index}",
                "owner": "SHORT",
                "route": "FIRST_DEFENDED_RETURN",
                "reason": "lower_global_utility",
                "entry": 100.0,
                "stop": 101.0,
                "target": 98.0,
                "selected": False,
                "posterior": 0.4,
            }
        ]
        self.index += 1
        return output

    def on_execution_feedback(self, feedback: ExecutionFeedback) -> None:
        self.feedback.append(feedback)

    def intent_replay_context(self, intent_id: str) -> dict[str, Any]:
        return {
            "source_id": "BTC:PDH:7",
            "owner": "LONG",
            "route": "DIRECT_RELEASE",
            "posterior": 0.71,
        }

    def drain_replay_opportunities(self):
        records, self._opportunities = self._opportunities, []
        return records


def _long_intent(frame: MarketFrame, intent_id: str = "i-1") -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        symbol="BTCUSDT",
        side="LONG",
        decision_time_ns=frame.close_time_ns,
        entry=100.0,
        stop=99.0,
        target=102.0,
    )


def test_decision_bar_cannot_fill_and_fill_bar_target_is_not_credited() -> None:
    frames = [
        _frame(0, btc=(100.0, 102.5, 99.5, 102.0)),
        _frame(1, btc=(101.0, 102.5, 99.5, 101.5)),
        _frame(2, btc=(101.5, 101.8, 99.6, 100.5)),
        _frame(3, btc=(100.5, 102.1, 100.1, 102.0)),
    ]
    policy = _Policy({0: PolicyOutput(intents=(_long_intent(frames[0]),))})
    result = run_fast_replay(policy, frames, no_trade_sample_limit=2)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_time_ns == frames[1].close_time_ns
    assert trade.exit_time_ns == frames[3].close_time_ns
    assert trade.outcome == "TARGET"
    assert trade.entry == pytest.approx(101.0)  # stop-entry gap at the next open
    assert trade.gross_r == pytest.approx(1.0)
    assert trade.mfe_r == pytest.approx(1.0)
    assert trade.mae_r == pytest.approx(-1.5)
    assert (trade.source_id, trade.owner, trade.route) == (
        "BTC:PDH:7", "LONG", "DIRECT_RELEASE",
    )
    assert [item.status for item in policy.feedback] == [
        "SUBMITTED", "FILLED", "TARGET_FILLED",
    ]
    assert result.no_trade_opportunities_seen == 4
    assert len(result.sampled_no_trades) == 2


def test_fill_bar_stop_wins_and_adverse_gap_uses_open() -> None:
    frames = [
        _frame(0),
        _frame(1, btc=(100.2, 100.5, 98.8, 99.2)),
    ]
    policy = _Policy({0: PolicyOutput(intents=(_long_intent(frames[0]),))})
    result = run_fast_replay(policy, frames)
    trade = result.trades[0]
    assert trade.outcome == "STOP"
    assert trade.exit_price == 99.0
    assert trade.mfe_r == 0.0
    assert trade.gross_r == pytest.approx(-1.2)
    assert trade.mae_r == pytest.approx(-1.2)

    # A position filled first, then gaps through its stop on a later bar.
    frames = [
        _frame(0),
        _frame(1, btc=(100.2, 100.3, 99.5, 100.0)),
        _frame(2, btc=(98.5, 102.5, 98.0, 101.0)),
    ]
    policy = _Policy({0: PolicyOutput(intents=(_long_intent(frames[0], "gap"),))})
    trade = run_fast_replay(policy, frames).trades[0]
    assert trade.outcome == "STOP"
    assert trade.exit_price == 98.5
    assert trade.gross_r == pytest.approx(-1.7)
    assert trade.mfe_r == 0.0  # no unknowable favorable move credited on stop bar
    assert trade.mae_r == pytest.approx(-1.7)


def test_open_position_is_not_force_closed_at_end() -> None:
    frames = [_frame(0), _frame(1, btc=(100.2, 100.4, 99.5, 100.1))]
    policy = _Policy({0: PolicyOutput(intents=(_long_intent(frames[0]),))})
    result = run_fast_replay(policy, frames)
    assert result.open_intent_id == "i-1"
    assert result.trades[0].outcome == "OPEN"
    assert result.trades[0].exit_time_ns is None


def test_policy_cannot_emit_a_second_global_slot_intent() -> None:
    frames = [_frame(0), _frame(1, btc=(101.0, 101.5, 100.5, 101.0))]
    second = _long_intent(frames[1], "i-2")
    policy = _Policy(
        {
            0: PolicyOutput(intents=(_long_intent(frames[0]),)),
            1: PolicyOutput(intents=(second,)),
        }
    )
    with pytest.raises(FastReplayError, match="global slot was occupied"):
        run_fast_replay(policy, frames)


def _write_archive(root: Path, symbol: str, month: str, rows: list[list[str]]) -> Path:
    folder = root / symbol
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{symbol}-5m-{month}.zip"
    member = path.with_suffix(".csv").name
    fields = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
    ]
    from io import StringIO
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(fields)
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, buffer.getvalue())
    return path


def test_discovers_and_streams_cached_official_schema(tmp_path: Path) -> None:
    row = ["0", "100", "101", "99", "100", "10", "299999", "1000", "5", "4", "400", "0"]
    for symbol in MARKET_SYMBOLS:
        _write_archive(tmp_path, symbol, "2026-03", [row])
    archives = discover_binance_vision_5m(tmp_path, months=["2026-03"])
    frames = list(iter_synchronized_frames(archives))
    assert len(frames) == 1
    assert frames[0].bar("BTCUSDT").trade_count == 5
    assert frames[0].bar("XRPUSDT").taker_buy_quote_volume == 400.0


def test_writer_emits_trade_path_no_trade_sample_and_conventions(tmp_path: Path) -> None:
    frames = [_frame(0), _frame(1, btc=(100.2, 100.4, 99.5, 100.1))]
    policy = _Policy({0: PolicyOutput(intents=(_long_intent(frames[0]),))})
    result = run_fast_replay(policy, frames, no_trade_sample_limit=1)
    write_replay_result(result, tmp_path)
    summary = __import__("json").loads((tmp_path / "run.json").read_text())
    assert summary["causal_conventions"]["fill_bar_target_credit"] is False
    assert summary["causal_conventions"]["time_exit"] is False
    assert (tmp_path / "trade_paths.csv").read_text().startswith("intent_id,")
    assert len((tmp_path / "no_trade_sample.jsonl").read_text().splitlines()) == 1
