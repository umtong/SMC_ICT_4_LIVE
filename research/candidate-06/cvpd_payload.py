#!/usr/bin/env python3
# -*- coding: ascii -*-
"""Materialize the predeclared candidate-06 CVPD source bundle."""
from __future__ import annotations
import base64
import io
from pathlib import Path
import tarfile


def _repair_injected_engine_initialization(root: Path) -> None:
    """Inject CVPD only after the reusable base strategy has initialized.

    The base constructor eagerly builds the configured engine. CVPD requires a
    synchronized spot context argument unavailable to the generic selector, so
    initialization must use a harmless supported placeholder which is replaced
    before any bar can be processed. No market or risk rule changes here.
    """

    path = root / "cross_venue_nautilus_runner.py"
    text = path.read_text(encoding="utf-8")
    old = '''    class CrossVenueStrategy(BaseStrategy):
        def __init__(self, strategy_config: Any) -> None:
            super().__init__(
                strategy_config,
                observations=observations,
                logic_params=logic_params,
            )
            self._scenario_engine = CrossVenuePriceDiscoveryBifurcationEngine(
'''
    new = '''    class CrossVenueStrategy(BaseStrategy):
        def __init__(self, strategy_config: Any) -> None:
            base_logic_params = dict(logic_params)
            base_logic_params["engine"] = "LIQUIDITY_RESPONSE_BIFURCATION"
            super().__init__(
                strategy_config,
                observations=observations,
                logic_params=base_logic_params,
            )
            self._logic_params = dict(logic_params)
            self._scenario_engine = CrossVenuePriceDiscoveryBifurcationEngine(
'''
    if new in text:
        return
    if old not in text:
        raise RuntimeError("CVPD runner initialization anchor changed")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parent
    parts = sorted(root.glob("cvpd_payload.part-*"))
    if not parts:
        raise RuntimeError("CVPD payload parts are missing")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    raw = base64.b64decode(encoded, validate=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            target = Path(member.name)
            if member.isdir() or target.is_absolute() or ".." in target.parts or len(target.parts) != 1:
                raise RuntimeError(f"unsafe CVPD payload member: {member.name}")
        bundle.extractall(root, filter="data")
    _repair_injected_engine_initialization(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
