"""Small domain contracts shared by independent research candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Mapping


class ContractError(ValueError):
    """Raised when research evidence violates a causal or structural contract."""


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ResearchEvent:
    """One explainable observation or state transition.

    ``event_time_ns`` is where the market event belongs. ``observed_time_ns`` is
    when the algorithm could know it. Keeping both prevents future-confirmed
    structures from being treated as known at their visual pivot timestamp.
    """

    scenario_id: str
    instrument_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "scenario_id",
            "instrument_id",
            "event_type",
            "previous_state",
            "next_state",
            "reason_code",
        ):
            _require_text(name, getattr(self, name))

        if isinstance(self.event_time_ns, bool) or not isinstance(self.event_time_ns, int):
            raise ContractError("event_time_ns must be an integer")
        if isinstance(self.observed_time_ns, bool) or not isinstance(self.observed_time_ns, int):
            raise ContractError("observed_time_ns must be an integer")
        if self.event_time_ns < 0 or self.observed_time_ns < 0:
            raise ContractError("timestamps must be non-negative")
        if self.observed_time_ns < self.event_time_ns:
            raise ContractError("observed_time_ns cannot precede event_time_ns")
        if self.reference_price is not None:
            try:
                Decimal(self.reference_price)
            except Exception as exc:
                raise ContractError("reference_price must be a decimal string") from exc
        if not isinstance(self.details, Mapping):
            raise ContractError("details must be a mapping")

        safe_details = json.loads(json.dumps(dict(self.details), sort_keys=True, default=str))
        object.__setattr__(self, "details", safe_details)

    @property
    def event_id(self) -> str:
        payload = self.to_dict(include_event_id=False)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    def to_dict(self, *, include_event_id: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if include_event_id:
            payload["event_id"] = self.event_id
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchEvent":
        values = dict(payload)
        values.pop("event_id", None)
        return cls(**values)
