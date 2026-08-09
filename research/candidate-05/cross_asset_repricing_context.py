"""Process-local completed-state registry for one shared NautilusTrader node."""
from __future__ import annotations

from collections import defaultdict, deque
from threading import RLock

from cross_asset_repricing_logic import PeerAuctionState


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class CompletedCrossAssetContext:
    """Retain a small causal history for each project instrument."""

    def __init__(self, maxlen: int = 4) -> None:
        if maxlen < 2:
            raise ValueError("maxlen must retain at least current and prior states")
        self._maxlen = maxlen
        self._lock = RLock()
        self._states: dict[str, deque[PeerAuctionState]] = defaultdict(
            lambda: deque(maxlen=self._maxlen),
        )

    def reset(self) -> None:
        with self._lock:
            self._states.clear()

    def publish(self, state: PeerAuctionState) -> None:
        if state.symbol not in PROJECT_SYMBOLS:
            raise ValueError(f"unsupported project symbol: {state.symbol}")
        with self._lock:
            history = self._states[state.symbol]
            if history and state.ts_event < history[-1].ts_event:
                raise ValueError("cross-asset states must be published monotonically")
            if history and state.ts_event == history[-1].ts_event:
                history[-1] = state
            else:
                history.append(state)

    def prior_peer_states(
        self,
        *,
        current_symbol: str,
        current_ts: int,
    ) -> tuple[PeerAuctionState, ...]:
        """Return latest peer observations strictly before ``current_ts``."""
        with self._lock:
            result: list[PeerAuctionState] = []
            for symbol in PROJECT_SYMBOLS:
                if symbol == current_symbol:
                    continue
                history = self._states.get(symbol)
                if not history:
                    continue
                for state in reversed(history):
                    if state.ts_event < current_ts:
                        result.append(state)
                        break
            return tuple(result)

    def snapshot(self) -> dict[str, list[dict[str, float | int | str]]]:
        with self._lock:
            return {
                symbol: [
                    {
                        "symbol": item.symbol,
                        "ts_event": item.ts_event,
                        "return_atr": item.return_atr,
                        "flow_3m": item.flow_3m,
                        "efficiency_60s": item.efficiency_60s,
                        "depth_imbalance": item.depth_imbalance,
                    }
                    for item in history
                ]
                for symbol, history in self._states.items()
            }


SHARED_CROSS_ASSET_CONTEXT = CompletedCrossAssetContext()


def reset_shared_cross_asset_context() -> None:
    SHARED_CROSS_ASSET_CONTEXT.reset()


__all__ = [
    "CompletedCrossAssetContext",
    "PROJECT_SYMBOLS",
    "SHARED_CROSS_ASSET_CONTEXT",
    "reset_shared_cross_asset_context",
]
