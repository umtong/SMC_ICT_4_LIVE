from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from smc_ict_4.episode_policy_live.live_bars import (
    BinanceBarConflictError,
    BinanceBarDataError,
    BinanceBarGapError,
    DEFAULT_WARMUP_MINUTES,
    MAX_BINANCE_KLINE_PAGE,
    NS_PER_MINUTE,
    fetch_recent_binance_bars,
)


def kline(slot: int, *, close: str | None = None) -> list[object]:
    open_ms = slot * 60_000
    close_price = close or str(100.5 + (slot % 1_000) / 10_000)
    return [
        open_ms,
        "100",
        "101",
        "99",
        close_price,
        "10",
        open_ms + 59_999,
        "1000",
        10,
        "5",
        "550",
        "0",
    ]


class ArchiveTransport:
    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    def __call__(self, url: str, *, timeout_seconds: float) -> object:
        self.calls.append(url)
        self.timeout_seconds = timeout_seconds
        query = parse_qs(urlparse(url).query)
        limit = int(query["limit"][0])
        end_time = int(query["endTime"][0])
        eligible = [row for row in self.rows if int(row[0]) <= end_time]
        return eligible[-limit:]


def test_default_is_seven_completed_days() -> None:
    assert DEFAULT_WARMUP_MINUTES == 10_080
    assert MAX_BINANCE_KLINE_PAGE == 1_500


def test_paginates_backward_with_exact_request_bounds_and_canonical_clock() -> None:
    transport = ArchiveTransport([kline(slot) for slot in range(4_000)])
    started_ns = 4_000 * NS_PER_MINUTE + 1

    bars = fetch_recent_binance_bars(
        "BTCUSDT",
        limit=3_200,
        clock_ns=lambda: started_ns,
        transport=transport,
        endpoints=("https://primary",),
    )

    assert len(bars) == 3_200
    assert bars[0].open_time_ns == 800 * NS_PER_MINUTE
    assert bars[-1].open_time_ns == 3_999 * NS_PER_MINUTE
    assert all(bar.close_time_ns == bar.open_time_ns + NS_PER_MINUTE for bar in bars)
    assert [int(parse_qs(urlparse(url).query)["limit"][0]) for url in transport.calls] == [
        1_500,
        1_500,
        200,
    ]
    end_times = [int(parse_qs(urlparse(url).query)["endTime"][0]) for url in transport.calls]
    assert end_times == sorted(end_times, reverse=True)


def test_identical_page_overlap_is_deduplicated_and_history_remains_sorted() -> None:
    class OverlapTransport(ArchiveTransport):
        def __call__(self, url: str, *, timeout_seconds: float) -> object:
            payload = list(super().__call__(url, timeout_seconds=timeout_seconds))
            if len(self.calls) == 2:
                # Repeat the previous page's oldest row in place of one older
                # row.  The cursor still advances using the older row at slot 6.
                return [kline(6), kline(7)]
            return list(reversed(payload))

    transport = OverlapTransport([kline(slot) for slot in range(10)])
    bars = fetch_recent_binance_bars(
        "ETHUSDT",
        limit=5,
        page_limit=3,
        clock_ns=lambda: 10 * NS_PER_MINUTE + 1,
        transport=transport,
        endpoints=("https://primary",),
    )

    assert [bar.open_time_ns // NS_PER_MINUTE for bar in bars] == [5, 6, 7, 8, 9]
    assert len(transport.calls) == 3


def test_provider_returned_incomplete_current_bar_is_discarded() -> None:
    class CurrentRowTransport(ArchiveTransport):
        def __call__(self, url: str, *, timeout_seconds: float) -> object:
            payload = list(super().__call__(url, timeout_seconds=timeout_seconds))
            if len(self.calls) == 1:
                return [kline(8), kline(9), kline(10)]
            return payload

    transport = CurrentRowTransport([kline(slot) for slot in range(11)])
    bars = fetch_recent_binance_bars(
        "SOLUSDT",
        limit=3,
        page_limit=3,
        clock_ns=lambda: 10 * NS_PER_MINUTE + NS_PER_MINUTE // 2,
        transport=transport,
        endpoints=("https://primary",),
    )

    assert [bar.open_time_ns // NS_PER_MINUTE for bar in bars] == [7, 8, 9]
    assert len(transport.calls) == 2


def test_conflicting_overlap_fails_closed() -> None:
    class ConflictTransport(ArchiveTransport):
        def __call__(self, url: str, *, timeout_seconds: float) -> object:
            payload = list(super().__call__(url, timeout_seconds=timeout_seconds))
            if len(self.calls) == 2:
                payload.append(kline(7, close="100.75"))
            return payload

    transport = ConflictTransport([kline(slot) for slot in range(10)])
    with pytest.raises(BinanceBarConflictError, match="share one minute"):
        fetch_recent_binance_bars(
            "XRPUSDT",
            limit=5,
            page_limit=3,
            clock_ns=lambda: 10 * NS_PER_MINUTE + 1,
            transport=transport,
            endpoints=("https://primary",),
        )


def test_gap_in_selected_completed_window_fails_closed() -> None:
    transport = ArchiveTransport([kline(slot) for slot in range(10) if slot != 7])
    with pytest.raises(BinanceBarGapError, match="non-contiguous"):
        fetch_recent_binance_bars(
            "BTCUSDT",
            limit=5,
            clock_ns=lambda: 10 * NS_PER_MINUTE + 1,
            transport=transport,
            endpoints=("https://primary",),
        )


def test_non_progressing_page_is_bounded_and_fails() -> None:
    class StuckTransport:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _url: str, *, timeout_seconds: float) -> object:
            self.calls += 1
            return [kline(9)]

    transport = StuckTransport()
    with pytest.raises(BinanceBarDataError, match="non-progressing"):
        fetch_recent_binance_bars(
            "BTCUSDT",
            limit=2,
            page_limit=1,
            clock_ns=lambda: 10 * NS_PER_MINUTE + 1,
            transport=transport,
            endpoints=("https://primary",),
        )
    assert transport.calls == 2


def test_each_page_retries_public_hosts_but_never_exceeds_them() -> None:
    archive = ArchiveTransport([kline(slot) for slot in range(3)])
    calls: list[str] = []

    def transport(url: str, *, timeout_seconds: float) -> object:
        calls.append(url)
        if url.startswith("https://primary"):
            raise TimeoutError("injected")
        return archive(url, timeout_seconds=timeout_seconds)

    bars = fetch_recent_binance_bars(
        "BTCUSDT",
        limit=2,
        clock_ns=lambda: 3 * NS_PER_MINUTE + 1,
        transport=transport,
        endpoints=("https://primary", "https://fallback"),
    )

    assert len(bars) == 2
    assert len(calls) == 2
    assert all("/fapi/v1/klines" in url for url in calls)
    assert all("interval=1m" in url and "symbol=BTCUSDT" in url for url in calls)


def test_malformed_close_clock_fails_before_policy_can_observe_it() -> None:
    row = kline(1)
    row[6] = int(row[6]) - 1
    transport = ArchiveTransport([row])
    with pytest.raises(BinanceBarDataError, match="non-canonical"):
        fetch_recent_binance_bars(
            "BTCUSDT",
            limit=1,
            clock_ns=lambda: 3 * NS_PER_MINUTE,
            transport=transport,
            endpoints=("https://primary",),
        )
