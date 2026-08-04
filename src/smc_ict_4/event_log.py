"""JSONL persistence and causal validation for research events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

from .contracts import ContractError, ResearchEvent


class EventLogError(ContractError):
    """Raised when an event sequence is internally inconsistent."""


def validate_events(events: Iterable[ResearchEvent]) -> list[ResearchEvent]:
    materialized = list(events)
    last_global_observed = -1
    last_by_scenario: dict[str, ResearchEvent] = {}
    seen_ids: set[str] = set()

    for index, event in enumerate(materialized):
        if event.event_id in seen_ids:
            raise EventLogError(f"duplicate event at index {index}: {event.event_id}")
        seen_ids.add(event.event_id)

        if event.observed_time_ns < last_global_observed:
            raise EventLogError(
                f"observed time moved backwards at index {index}: "
                f"{event.observed_time_ns} < {last_global_observed}",
            )
        last_global_observed = event.observed_time_ns

        previous = last_by_scenario.get(event.scenario_id)
        if previous is not None:
            if event.previous_state != previous.next_state:
                raise EventLogError(
                    f"scenario {event.scenario_id!r} state chain broke: "
                    f"expected previous_state={previous.next_state!r}, "
                    f"got {event.previous_state!r}",
                )
            if event.observed_time_ns < previous.observed_time_ns:
                raise EventLogError(
                    f"scenario {event.scenario_id!r} observed time moved backwards",
                )
        last_by_scenario[event.scenario_id] = event

    return materialized


def write_events(path: str | Path, events: Iterable[ResearchEvent]) -> Path:
    destination = Path(path)
    validated = validate_events(events)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for event in validated:
            stream.write(json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False))
            stream.write("\n")
    temporary.replace(destination)
    return destination


def read_events(path: str | Path) -> Iterator[ResearchEvent]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                yield ResearchEvent.from_dict(payload)
            except Exception as exc:
                raise EventLogError(f"invalid event on line {line_number}: {exc}") from exc


def validate_event_file(path: str | Path) -> list[ResearchEvent]:
    return validate_events(read_events(path))
