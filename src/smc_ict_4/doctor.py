"""Environment checks kept intentionally small and actionable."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import platform
import re
import subprocess
import sys
from typing import Iterable


EXPECTED_PYTHON = (3, 13)
EXPECTED_NAUTILUS = "1.230.0"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _nautilus_version() -> str:
    try:
        return metadata.version("nautilus_trader")
    except metadata.PackageNotFoundError:
        return "not-installed"


def _glibc_check() -> Check:
    if platform.system() != "Linux":
        return Check("glibc", True, "not applicable")
    try:
        output = subprocess.run(
            ["ldd", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()[0]
        match = re.search(r"(\d+)\.(\d+)", output)
        if not match:
            return Check("glibc", False, f"unable to parse: {output}")
        version = tuple(map(int, match.groups()))
        return Check("glibc", version >= (2, 35), f"{version[0]}.{version[1]} (need >= 2.35)")
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("glibc", False, str(exc))


def checks() -> list[Check]:
    python_ok = sys.version_info[:2] == EXPECTED_PYTHON
    nautilus = _nautilus_version()
    return [
        Check("python", python_ok, f"{platform.python_version()} (need 3.13.x)"),
        Check("nautilus_trader", nautilus == EXPECTED_NAUTILUS, f"{nautilus} (need {EXPECTED_NAUTILUS})"),
        _glibc_check(),
    ]


def render(results: Iterable[Check]) -> str:
    return "\n".join(f"[{'OK' if item.ok else 'FAIL'}] {item.name}: {item.detail}" for item in results)
