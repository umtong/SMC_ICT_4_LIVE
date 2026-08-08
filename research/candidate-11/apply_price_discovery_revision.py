#!/usr/bin/env python3
"""Idempotently materialize Candidate 11's price-discovery leadership revision."""
from __future__ import annotations

from pathlib import Path

MARKER = "FOLLOWER_FAR_IDIOSYNCRATIC_PRICE_DISCOVERY"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def apply(root: Path) -> int:
    path = root / "market_leadership.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        changed = 0
    else:
        pairs = [
            (
'''    candidate_event_move: float | None
    peer_event_median: float | None
    confirmation_impulse: float | None
''',
'''    candidate_event_move: float | None
    peer_event_median: float | None
    confirmation_impulse: float | None
    trailing_direction_rank: int | None
    event_direction_rank: int | None
    event_path_efficiency: float | None
    event_standardized_displacement: float | None
''', "decision diagnostics"),
            (
'''            "candidate_event_move": self.candidate_event_move,
            "peer_event_median": self.peer_event_median,
            "confirmation_impulse": self.confirmation_impulse,
''',
'''            "candidate_event_move": self.candidate_event_move,
            "peer_event_median": self.peer_event_median,
            "confirmation_impulse": self.confirmation_impulse,
            "trailing_direction_rank": self.trailing_direction_rank,
            "event_direction_rank": self.event_direction_rank,
            "event_path_efficiency": self.event_path_efficiency,
            "event_standardized_displacement": self.event_standardized_displacement,
''', "serialized diagnostics"),
            (
'''        minimum_follower_confirmation_impulse: float = 1.0,
    ) -> None:
''',
'''        minimum_follower_confirmation_impulse: float = 1.0,
        minimum_idiosyncratic_event_efficiency: float = 0.10,
        minimum_idiosyncratic_event_displacement: float = 0.50,
    ) -> None:
''', "price-discovery thresholds"),
            (
'''        if (
            not isfinite(minimum_follower_confirmation_impulse)
            or minimum_follower_confirmation_impulse <= 0
        ):
            raise ValueError("minimum follower confirmation impulse must be positive")
''',
'''        if (
            not isfinite(minimum_follower_confirmation_impulse)
            or minimum_follower_confirmation_impulse <= 0
        ):
            raise ValueError("minimum follower confirmation impulse must be positive")
        if (
            not isfinite(minimum_idiosyncratic_event_efficiency)
            or not 0 < minimum_idiosyncratic_event_efficiency <= 1
        ):
            raise ValueError("idiosyncratic event efficiency must be in (0, 1]")
        if (
            not isfinite(minimum_idiosyncratic_event_displacement)
            or minimum_idiosyncratic_event_displacement <= 0
        ):
            raise ValueError("idiosyncratic event displacement must be positive")
''', "threshold validation"),
            (
'''        self.minimum_follower_confirmation_impulse = float(
            minimum_follower_confirmation_impulse,
        )
''',
'''        self.minimum_follower_confirmation_impulse = float(
            minimum_follower_confirmation_impulse,
        )
        self.minimum_idiosyncratic_event_efficiency = float(
            minimum_idiosyncratic_event_efficiency,
        )
        self.minimum_idiosyncratic_event_displacement = float(
            minimum_idiosyncratic_event_displacement,
        )
''', "threshold state"),
            (
'''            confirmation_impulse: float | None = None,
        ) -> LeadershipDecision:
''',
'''            confirmation_impulse: float | None = None,
            trailing_direction_rank: int | None = None,
            event_direction_rank: int | None = None,
            event_path_efficiency: float | None = None,
            event_standardized_displacement: float | None = None,
        ) -> LeadershipDecision:
''', "decision factory parameters"),
            (
'''                peer_event_median=peer_event_median,
                confirmation_impulse=confirmation_impulse,
            )
''',
'''                peer_event_median=peer_event_median,
                confirmation_impulse=confirmation_impulse,
                trailing_direction_rank=trailing_direction_rank,
                event_direction_rank=event_direction_rank,
                event_path_efficiency=event_path_efficiency,
                event_standardized_displacement=event_standardized_displacement,
            )
''', "decision factory fields"),
        ]
        for old, new, label in pairs:
            source = replace_once(source, old, new, label)

        source = replace_once(
            source,
'''    def decide(
        self,
''',
'''    def _event_recovery_state(
        self,
        symbol: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
        direction: str,
    ) -> tuple[float, float] | None:
        """Return path efficiency and pre-event-volatility displacement."""
        points = list(self._history[symbol])
        event = [
            point for point in points
            if sweep_ts_ns <= point.ts_ns <= confirmation_ts_ns
        ]
        if (
            len(event) < 2
            or event[0].ts_ns != sweep_ts_ns
            or event[-1].ts_ns != confirmation_ts_ns
        ):
            return None
        event_returns = [
            log(curr.close / prev.close)
            for prev, curr in zip(event, event[1:])
        ]
        sign = 1.0 if direction == "LONG" else -1.0
        signed_net = sign * log(event[-1].close / event[0].close)
        efficiency = signed_net / max(sum(abs(x) for x in event_returns), 1e-12)
        prior = [point for point in points if point.ts_ns < sweep_ts_ns]
        required = self.confirmation_impulse_lookback_bars + 1
        if len(prior) < required:
            return None
        prior = prior[-required:]
        prior_returns = [
            log(curr.close / prev.close)
            for prev, curr in zip(prior, prior[1:])
        ]
        baseline_rms = sqrt(sum(x * x for x in prior_returns) / len(prior_returns))
        standardized = signed_net / max(
            baseline_rms * sqrt(len(event_returns)),
            1e-12,
        )
        return efficiency, standardized

    def decide(
        self,
''',
            "event recovery method",
        )
        source = replace_once(
            source,
'''        directional_rank = 1 + sum(
            value > directional_returns[symbol]
            for peer, value in directional_returns.items()
            if peer != symbol
        )
        top_half_limit = max(1, (len(self.symbols) + 1) // 2)
        directionally_supported = directional_rank <= top_half_limit
        event_recovered = candidate_move > 0.0 and candidate_move > peer_median

        common = {
            "peer_returns": peer_returns,
            "directional_returns": directional_returns,
            "directional_trend_scores": trend_scores,
            "candidate_event_move": candidate_move,
            "peer_event_median": peer_median,
            "confirmation_impulse": confirmation_impulse,
        }
''',
'''        directional_rank = 1 + sum(
            value > directional_returns[symbol]
            for peer, value in directional_returns.items()
            if peer != symbol
        )
        event_rank = 1 + sum(value > candidate_move for value in signed_peer_moves)
        event_state = self._event_recovery_state(
            symbol, sweep_ts_ns, confirmation_ts_ns, direction,
        )
        event_efficiency = None if event_state is None else event_state[0]
        event_displacement = None if event_state is None else event_state[1]
        top_half_limit = max(1, (len(self.symbols) + 1) // 2)
        directionally_supported = directional_rank <= top_half_limit
        event_recovered = candidate_move > 0.0 and candidate_move > peer_median

        common = {
            "peer_returns": peer_returns,
            "directional_returns": directional_returns,
            "directional_trend_scores": trend_scores,
            "candidate_event_move": candidate_move,
            "peer_event_median": peer_median,
            "confirmation_impulse": confirmation_impulse,
            "trailing_direction_rank": directional_rank,
            "event_direction_rank": event_rank,
            "event_path_efficiency": event_efficiency,
            "event_standardized_displacement": event_displacement,
        }
''',
            "rank and event state",
        )
        source = replace_once(
            source,
'''        if scenario == "AAC":
            if symbol != leader:
                return decision(False, "FOLLOWER_AAC_WITHOUT_LEADERSHIP", leader, **common)
            if not (directionally_supported and event_recovered):
                return decision(False, "AAC_WITHOUT_DIRECTIONAL_ACCEPTANCE", leader, **common)
            return decision(True, "LEADER_AAC_DIRECTIONAL_ACCEPTANCE", leader, **common)

        if symbol == leader:
            if directionally_supported:
                reason = "LEADER_DIRECTIONAL_ALIGNMENT"
            elif event_recovered:
                reason = "LEADER_EVENT_RECOVERY"
            else:
                return decision(False, "LEADER_DIRECTIONAL_DISAGREEMENT", leader, **common)
            return decision(True, reason, leader, **common)
''',
'''        if scenario == "AAC":
            if symbol != leader:
                return decision(False, "FOLLOWER_AAC_WITHOUT_LEADERSHIP", leader, **common)
            if not event_recovered:
                return decision(False, "AAC_WITHOUT_EVENT_ACCEPTANCE", leader, **common)
            return decision(True, "LEADER_AAC_EVENT_ACCEPTANCE", leader, **common)

        if symbol == leader:
            if directionally_supported:
                reason = "LEADER_DIRECTIONAL_ALIGNMENT"
            elif event_recovered and directional_rank < len(self.symbols):
                reason = "LEADER_EVENT_RECOVERY"
            elif event_recovered:
                return decision(
                    False,
                    "LEADER_EVENT_RECOVERY_WITHOUT_DIRECTIONAL_SUPPORT",
                    leader,
                    **common,
                )
            else:
                return decision(False, "LEADER_DIRECTIONAL_DISAGREEMENT", leader, **common)
            return decision(True, reason, leader, **common)
''',
            "leader state separation",
        )
        source = replace_once(
            source,
'''            if confirmation_impulse < self.minimum_follower_confirmation_impulse:
                return decision(
                    False,
                    "FOLLOWER_FAR_WEAK_LOCAL_DISPLACEMENT",
                    leader,
                    **common,
                )
            return decision(True, "FOLLOWER_FAR_UNANIMOUS_PEERS", leader, **common)

        relative_recovery = (
            directionally_supported
            and event_recovered
            and any(value > 0.0 for value in signed_peer_moves)
        )
        if relative_recovery:
            return decision(
                True,
                "FOLLOWER_FAR_DIRECTIONAL_LEADER_RECOVERY",
                leader,
                **common,
            )
        return decision(False, "FOLLOWER_FAR_PEER_DISAGREEMENT", leader, **common)
''',
'''            if confirmation_impulse < self.minimum_follower_confirmation_impulse:
                return decision(
                    False,
                    "FOLLOWER_FAR_WEAK_LOCAL_DISPLACEMENT",
                    leader,
                    **common,
                )
            if event_rank == len(self.symbols):
                return decision(False, "FOLLOWER_FAR_EVENT_LAGGARD", leader, **common)
            return decision(True, "FOLLOWER_FAR_UNANIMOUS_PEERS", leader, **common)

        relative_recovery = (
            directionally_supported
            and event_recovered
            and any(value > 0.0 for value in signed_peer_moves)
        )
        if relative_recovery:
            return decision(
                True,
                "FOLLOWER_FAR_DIRECTIONAL_LEADER_RECOVERY",
                leader,
                **common,
            )
        idiosyncratic_price_discovery = (
            directional_rank == 1
            and event_rank == 1
            and candidate_move > 0.0
            and event_efficiency is not None
            and event_efficiency >= self.minimum_idiosyncratic_event_efficiency
            and event_displacement is not None
            and event_displacement >= self.minimum_idiosyncratic_event_displacement
        )
        if idiosyncratic_price_discovery:
            return decision(
                True,
                "FOLLOWER_FAR_IDIOSYNCRATIC_PRICE_DISCOVERY",
                leader,
                **common,
            )
        return decision(False, "FOLLOWER_FAR_PEER_DISAGREEMENT", leader, **common)
''',
            "follower state separation",
        )
        path.write_text(source, encoding="utf-8")
        changed = 1

    test_path = root / "test_market_leadership.py"
    tests = test_path.read_text(encoding="utf-8")
    updated = tests.replace(
        "LEADER_AAC_DIRECTIONAL_ACCEPTANCE",
        "LEADER_AAC_EVENT_ACCEPTANCE",
    ).replace(
        "AAC_WITHOUT_DIRECTIONAL_ACCEPTANCE",
        "AAC_WITHOUT_EVENT_ACCEPTANCE",
    )
    if updated != tests:
        test_path.write_text(updated, encoding="utf-8")
        changed += 1
    return changed


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"price-discovery leadership revisions applied: {apply(root)}")


if __name__ == "__main__":
    main()
