"""Strictly prior peer microstructure state for isolated SMT experiments."""
from __future__ import annotations

from collections import defaultdict, deque
from threading import RLock

from isolated_smt_logic import PeerMicroState


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class IsolatedSmtContext:
    def __init__(self, maxlen: int = 4) -> None:
        self._maxlen = maxlen
        self._lock = RLock()
        self._states: dict[str, deque[PeerMicroState]] = defaultdict(
            lambda: deque(maxlen=maxlen),
        )

    def reset(self) -> None:
        with self._lock:
            self._states.clear()

    def publish(self, state: PeerMicroState) -> None:
        if state.symbol not in PROJECT_SYMBOLS:
            raise ValueError(f"unsupported project symbol: {state.symbol}")
        with self._lock:
            history = self._states[state.symbol]
            if history and state.ts_event < history[-1].ts_event:
                raise ValueError("isolated SMT peer states must be monotonic")
            if history and state.ts_event == history[-1].ts_event:
                history[-1] = state
            else:
                history.append(state)

    def prior_peer_states(
        self,
        *,
        current_symbol: str,
        current_ts: int,
    ) -> tuple[PeerMicroState, ...]:
        with self._lock:
            result: list[PeerMicroState] = []
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


SHARED_ISOLATED_SMT_CONTEXT = IsolatedSmtContext()


def reset_shared_isolated_smt_context() -> None:
    SHARED_ISOLATED_SMT_CONTEXT.reset()


__all__ = [
    "IsolatedSmtContext",
    "PROJECT_SYMBOLS",
    "SHARED_ISOLATED_SMT_CONTEXT",
    "reset_shared_isolated_smt_context",
]
