#!/usr/bin/env python3
"""Single-variable ablation of V46 reclaim-basis confirmation.

The directional parent session, first next-session VWAP pullback, counter-flow
pullback, parent-side price reclaim, parent-side executed flow and return,
state-interval OI route, stop and causal target are unchanged. Only positive
five-minute basis change at the reclaim bar is removed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile


BASE = Path(__file__).with_name("directional_session_vwap_reclaim_compiler.py")
OLD_GATE = "for value in (flow, ret, basis)"
NEW_GATE = "for value in (flow, ret)"


def transform_source(source: str) -> str:
    if source.count(OLD_GATE) != 1:
        raise RuntimeError("V46 reclaim-basis gate marker is not unique")
    changed = source.replace(OLD_GATE, NEW_GATE, 1)
    changed = changed.replace(
        '"compiler": "candidate-04-directional-session-vwap-reclaim-v1",',
        '"compiler": "candidate-04-directional-session-vwap-no-basis-ablation",',
    )
    changed = changed.replace(
        '"candidate": "candidate-04-directional-session-vwap-reclaim-v1",',
        '"candidate": "candidate-04-directional-session-vwap-no-basis-ablation",',
    )
    changed = changed.replace(
        '"close back through previous session VWAP within three bars "\n                "with parent-side flow, return and five-minute basis"',
        '"close back through previous session VWAP within three bars "\n                "with parent-side flow and return; five-minute basis ablated"',
    )
    return changed


def load_ablation_module():
    source = transform_source(BASE.read_text(encoding="utf-8"))
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix="_v46_no_basis.py",
        prefix=".candidate04_",
        dir=BASE.parent,
        delete=False,
        encoding="utf-8",
    )
    path = Path(handle.name)
    try:
        handle.write(source)
        handle.close()
        name = "candidate04_directional_session_vwap_no_basis"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load V46 basis ablation")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module, path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main() -> None:
    module, path = load_ablation_module()
    try:
        module.v22.main()
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
