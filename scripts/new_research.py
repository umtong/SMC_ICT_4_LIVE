#!/usr/bin/env python3
"""Create one independent research branch and worktree from a common base."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys


def run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("name must contain an ASCII letter or digit")
    return slug


def create(name: str, *, base: str, root: Path | None) -> tuple[str, Path]:
    slug = slugify(name)
    repo = Path(run("git", "rev-parse", "--show-toplevel")).resolve()
    branch = f"research/{slug}"
    worktree_root = root.resolve() if root else repo.parent / "worktrees"
    worktree = worktree_root / slug

    if worktree.exists():
        raise FileExistsError(f"worktree path already exists: {worktree}")

    worktree_root.mkdir(parents=True, exist_ok=True)
    run("git", "worktree", "add", "-b", branch, str(worktree), base, cwd=repo)

    template = repo / "research" / "_template"
    target = worktree / "research" / slug
    shutil.copytree(template, target)
    readme = target / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("{{CANDIDATE}}", slug), encoding="utf-8")
    config = target / "config.json"
    config.write_text(config.read_text(encoding="utf-8").replace("{{CANDIDATE}}", slug), encoding="utf-8")
    return branch, worktree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="candidate name, for example candidate-a")
    parser.add_argument("--base", default="main", help="common starting ref (default: main)")
    parser.add_argument("--root", type=Path, help="worktree parent directory")
    args = parser.parse_args(argv)

    try:
        branch, worktree = create(args.name, base=args.base, root=args.root)
    except (ValueError, FileExistsError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"branch:   {branch}")
    print(f"worktree: {worktree}")
    print(f"next:     cd {worktree}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
