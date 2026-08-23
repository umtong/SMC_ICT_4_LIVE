#!/usr/bin/env python3
"""Launch candidate 4t with artifact-level period identity.

Downloaded action artifacts contain nested dated diagnostics. They belong to one
predeclared evaluation window and must share one period/account. This adapter
prevents inner file or directory dates from creating artificial parallel accounts.
"""
from __future__ import annotations

from pathlib import Path

import candidate_4t_policy as policy


def artifact_period(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    return relative.parts[0] if len(relative.parts) > 1 else path.parent.name


policy._period_from_path = artifact_period

if __name__ == "__main__":
    policy.main()
