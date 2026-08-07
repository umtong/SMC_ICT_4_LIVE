#!/usr/bin/env python3
"""Install Candidate 16's failed-FAR state into the frozen portfolio runner."""
from __future__ import annotations

import argparse
from pathlib import Path

OLD_IMPORT = "from semantic_logic import install as _install_semantic_logic\n"
NEW_IMPORT = (
    "from semantic_logic import install as _install_semantic_logic\n"
    "from candidate16_failed_far import install as _install_candidate16_failed_far\n"
)
OLD_INSTALL = "_install_semantic_logic()\n\n_BASE = Path"
NEW_INSTALL = "_install_semantic_logic()\n_install_candidate16_failed_far()\n\n_BASE = Path"


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW_IMPORT in source and NEW_INSTALL in source:
        return False
    if source.count(OLD_IMPORT) != 1 or source.count(OLD_INSTALL) != 1:
        raise RuntimeError("Candidate 14 runner install anchors changed")
    source = source.replace(OLD_IMPORT, NEW_IMPORT, 1)
    source = source.replace(OLD_INSTALL, NEW_INSTALL, 1)
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate16 runner install patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
