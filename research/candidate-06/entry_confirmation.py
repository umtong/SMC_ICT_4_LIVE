"""Pure causal gate for continuation defense immediately before order submission."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DefenseCheck:
    mode: str
    direction: str
    boundary: float
    signal_reference: float
    open: float
    close: float
    flow_ratio: float

    @property
    def boundary_held(self) -> bool:
        if self.direction == "LONG":
            return self.close > self.boundary
        if self.direction == "SHORT":
            return self.close < self.boundary
        raise ValueError(f"unsupported direction: {self.direction}")

    @property
    def directional_body(self) -> bool:
        if self.direction == "LONG":
            return self.close > self.open
        return self.close < self.open

    @property
    def directional_flow(self) -> bool:
        if self.direction == "LONG":
            return self.flow_ratio >= 0.0
        return self.flow_ratio <= 0.0

    @property
    def reference_held(self) -> bool:
        if self.direction == "LONG":
            return self.close >= self.signal_reference
        return self.close <= self.signal_reference


def continuation_defense_passes(check: DefenseCheck) -> bool:
    """Return whether the next completed bar still supports the SAC premise.

    Modes are qualitative state alternatives, not tuned score thresholds.  Every
    non-disabled mode first requires the accepted boundary to remain held.
    """
    mode = check.mode.upper()
    if mode in {"NONE", "BOUNDARY_ONLY"}:
        return check.boundary_held
    if not check.boundary_held:
        return False
    if mode == "DIRECTIONAL_BODY":
        return check.directional_body
    if mode == "DIRECTIONAL_FLOW":
        return check.directional_flow
    if mode == "BODY_AND_FLOW":
        return check.directional_body and check.directional_flow
    if mode == "REFERENCE_HOLD":
        return check.reference_held
    if mode == "REFERENCE_HOLD_AND_FLOW":
        return check.reference_held and check.directional_flow
    raise ValueError(f"unsupported SAC entry confirmation mode: {check.mode}")
