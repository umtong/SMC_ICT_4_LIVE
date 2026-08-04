"""Reproducible data and run manifests with no external service dependency."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Iterable, Mapping


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(args: list[str], default: str = "unknown") -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip() or default
    except (OSError, subprocess.SubprocessError):
        return default


def git_state() -> dict[str, Any]:
    status = _git_value(["status", "--porcelain"], default="")
    branch = _git_value(["branch", "--show-current"], default="")
    if not branch:
        branch = os.getenv("GITHUB_HEAD_REF") or os.getenv("GITHUB_REF_NAME") or "unknown"
    return {
        "branch": branch,
        "commit": _git_value(["rev-parse", "HEAD"]),
        "dirty": bool(status),
    }


def installed_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "not-installed"


def write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


@dataclass(frozen=True, slots=True)
class DataFile:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DataManifest:
    dataset: str
    created_at_utc: str
    root: str
    files: tuple[DataFile, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_data_manifest(
    root: str | Path,
    *,
    dataset: str,
    include: Iterable[Path] | None = None,
    metadata_values: Mapping[str, Any] | None = None,
) -> DataManifest:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"data root does not exist: {root_path}")

    candidates = list(include) if include is not None else [p for p in root_path.rglob("*") if p.is_file()]
    files: list[DataFile] = []
    for candidate in sorted((Path(p).resolve() for p in candidates), key=str):
        try:
            relative = candidate.relative_to(root_path)
        except ValueError as exc:
            raise ValueError(f"file is outside data root: {candidate}") from exc
        files.append(
            DataFile(
                path=relative.as_posix(),
                size_bytes=candidate.stat().st_size,
                sha256=sha256_file(candidate),
            ),
        )

    if not files:
        raise ValueError("data manifest cannot be empty")

    return DataManifest(
        dataset=dataset,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        root=str(root_path),
        files=tuple(files),
        metadata=dict(metadata_values or {}),
    )


def write_data_manifest(path: str | Path, manifest: DataManifest) -> Path:
    return write_json_atomic(path, manifest.to_dict())


def create_run_manifest(
    *,
    run_id: str,
    candidate: str,
    config_path: str | Path | None = None,
    data_manifest_path: str | Path | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "candidate": candidate,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git": git_state(),
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
            "pid": os.getpid(),
            "nautilus_trader": installed_version("nautilus_trader"),
            "foundation": installed_version("smc-ict-4-live"),
        },
    }
    if config_path is not None:
        path = Path(config_path)
        payload["config"] = {"path": str(path), "sha256": sha256_file(path)}
    if data_manifest_path is not None:
        path = Path(data_manifest_path)
        payload["data_manifest"] = {"path": str(path), "sha256": sha256_file(path)}
    if extra:
        payload["extra"] = dict(extra)
    return payload
