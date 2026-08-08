#!/usr/bin/env python3
"""Install Candidate 28 after Candidate 16/C25 lifecycle hooks."""
from __future__ import annotations

import argparse
from pathlib import Path

OLD_IMPORT = (
    "from candidate16_failed_far import install as _install_candidate16_failed_far\n"
)
NEW_IMPORT = (
    "from candidate16_failed_far import install as _install_candidate16_failed_far\n"
    "from candidate28_quarter_hour_reload import install as _install_candidate28_quarter_hour_reload\n"
)
OLD_INSTALL = "_install_candidate16_failed_far()\n\n_BASE = Path"
NEW_INSTALL = (
    "_install_candidate16_failed_far()\n"
    "_install_candidate28_quarter_hour_reload()\n\n_BASE = Path"
)


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW_IMPORT in source and NEW_INSTALL in source:
        return False
    if source.count(OLD_IMPORT) != 1 or source.count(OLD_INSTALL) != 1:
        raise RuntimeError("Candidate 28 runner install anchors changed")
    source = source.replace(OLD_IMPORT, NEW_IMPORT, 1)
    source = source.replace(OLD_INSTALL, NEW_INSTALL, 1)
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate28 runner install patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
