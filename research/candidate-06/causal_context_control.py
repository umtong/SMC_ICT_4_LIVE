"""Pure helpers for pending-entry and open-position causal invalidation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def first_matching_reason(
    transitions: Iterable[Any],
    allowed_reason_codes: Iterable[str],
) -> str | None:
    allowed = {str(value) for value in allowed_reason_codes}
    if not allowed:
        return None
    for transition in transitions:
        reason = str(getattr(transition, "reason_code", ""))
        if reason in allowed:
            return reason
    return None


def signal_exit_contract(signal: Any) -> tuple[tuple[str, ...], bool]:
    details: Mapping[str, Any] = getattr(signal, "details", {}) or {}
    codes = tuple(str(value) for value in details.get("causal_exit_reason_codes", ()))
    exit_open = bool(details.get("causal_exit_open_position", False))
    return codes, exit_open
