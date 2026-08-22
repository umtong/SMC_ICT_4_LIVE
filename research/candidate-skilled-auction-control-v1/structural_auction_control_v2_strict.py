"""Fail-fast structural auction control v2.

The research policy must not silently degrade to the earlier channel-only policy.
This adapter requires the integrated natural-geometry response engine to be
constructible and then exposes the same v2 lifecycle controller interface.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from structural_auction_control_v2 import StructuralAuctionControlV2Bundle as _Base


def _required_natural_geometry(
    symbol: str,
    tick_size: float,
    minimum_gross_rr: float,
) -> Any:
    module = import_module("easychart_re1_skilled_integrated")
    bundle_type = getattr(module, "MultiScaleScenarioBundle", None)
    if bundle_type is None:
        candidates = [
            value
            for name, value in vars(module).items()
            if name.endswith("Bundle") and isinstance(value, type)
        ]
        if not candidates:
            raise RuntimeError("easychart_re1_skilled_integrated exposes no bundle")
        bundle_type = candidates[-1]

    attempts = (
        ((symbol, tick_size, minimum_gross_rr), {}),
        ((symbol, tick_size), {"minimum_gross_rr": minimum_gross_rr}),
        ((symbol, tick_size), {}),
        ((), {"symbol": symbol, "tick_size": tick_size, "minimum_gross_rr": minimum_gross_rr}),
    )
    errors: list[str] = []
    for args, kwargs in attempts:
        try:
            return bundle_type(*args, **kwargs)
        except TypeError as exc:
            errors.append(repr(exc))
    raise RuntimeError(
        "integrated natural geometry could not be constructed; refusing channel-only fallback: "
        + " | ".join(errors)
    )


class StructuralAuctionControlV2Bundle(_Base):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        if self.natural_geometry is None:
            self.natural_geometry = _required_natural_geometry(
                symbol,
                tick_size,
                self.minimum_gross_rr,
            )
            self.sources.append(("NATURAL_GEOMETRY_RESPONSE", self.natural_geometry))
        if len(self.sources) < 2:
            raise RuntimeError("structural v2 requires both channel-control and natural-geometry sensors")


MultiScaleScenarioBundle = StructuralAuctionControlV2Bundle
