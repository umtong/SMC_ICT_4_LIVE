#!/usr/bin/env python3
"""Run the pinned GHCR Python environment without a Docker daemon.

GitHub's slim hosted runners occasionally expose the Docker client without a
Docker daemon. This utility preserves the project's fixed prebuilt environment:
it pulls the exact digest through the OCI Distribution API, verifies every blob,
applies the immutable layers, and launches the image's Python interpreter with
its own glibc and virtual-environment site packages.

It does not install or rebuild NautilusTrader, and it does not implement any
backtesting behavior. Order, fill, fee, funding, position, margin, accounting and
NAV behavior remain the pinned NautilusTrader runtime.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def _request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = 5,
):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return urlopen(Request(url, headers=headers or {}), timeout=180)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt + 1 == attempts:
                break
            time.sleep(min(30, 2**attempt))
    raise RuntimeError(f"request failed after {attempts} attempts: {url}") from last


def _json_request(url: str, *, headers: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    with _request(url, headers=headers) as response:
        payload = json.load(response)
        return payload, {key.lower(): value for key, value in response.headers.items()}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _registry_token(*, repository: str, username: str, password: str) -> str:
    query = urlencode(
        {
            "service": "ghcr.io",
            "scope": f"repository:{repository}:pull",
        }
    )
    basic = base64.b64encode(f"{username}:{password}".encode()).decode()
    payload, _ = _json_request(
        f"https://ghcr.io/token?{query}",
        headers={"Authorization": f"Basic {basic}", "User-Agent": "smc4-pinned-oci"},
    )
    token = payload.get("token") or payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("GHCR token response did not contain a bearer token")
    return token


def _manifest(
    *,
    repository: str,
    reference: str,
    bearer: str,
) -> tuple[dict[str, Any], str]:
    url = f"https://ghcr.io/v2/{repository}/manifests/{reference}"
    payload, headers = _json_request(
        url,
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": MANIFEST_ACCEPT,
            "User-Agent": "smc4-pinned-oci",
        },
    )
    digest = headers.get("docker-content-digest", reference)
    return payload, digest


def _select_amd64_manifest(
    *,
    repository: str,
    reference: str,
    bearer: str,
) -> tuple[dict[str, Any], str]:
    manifest, digest = _manifest(repository=repository, reference=reference, bearer=bearer)
    media_type = str(manifest.get("mediaType", ""))
    if media_type not in {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }:
        return manifest, digest
    descriptors = manifest.get("manifests")
    if not isinstance(descriptors, list):
        raise RuntimeError("OCI index is missing manifests")
    for descriptor in descriptors:
        platform = descriptor.get("platform", {})
        if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
            child = str(descriptor["digest"])
            return _manifest(repository=repository, reference=child, bearer=bearer)
    raise RuntimeError("pinned OCI index has no linux/amd64 manifest")


def _download_blob(
    *,
    repository: str,
    digest: str,
    bearer: str,
    destination: Path,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and _sha256_file(destination) == digest:
        return destination
    temporary = destination.with_suffix(destination.suffix + ".partial")
    temporary.unlink(missing_ok=True)
    url = f"https://ghcr.io/v2/{repository}/blobs/{digest}"
    with _request(
        url,
        headers={"Authorization": f"Bearer {bearer}", "User-Agent": "smc4-pinned-oci"},
    ) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    actual = _sha256_file(temporary)
    if actual != digest:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"OCI blob checksum mismatch: expected={digest} actual={actual}")
    temporary.replace(destination)
    return destination


def _safe_member_name(name: str) -> str:
    value = name.removeprefix("./")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise RuntimeError(f"unsafe OCI layer member: {name!r}")
    return path.as_posix()


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _apply_layer(layer: Path, rootfs: Path) -> None:
    with tarfile.open(layer, mode="r:*") as archive:
        members = archive.getmembers()
        whiteouts: list[tarfile.TarInfo] = []
        extractable: list[tarfile.TarInfo] = []
        for member in members:
            member.name = _safe_member_name(member.name)
            base = PurePosixPath(member.name).name
            if base.startswith(".wh."):
                whiteouts.append(member)
            elif member.ischr() or member.isblk() or member.isfifo():
                # Device nodes are irrelevant for the Python research runtime
                # and cannot be created by an unprivileged hosted runner.
                continue
            else:
                member.uid = os.getuid()
                member.gid = os.getgid()
                member.uname = ""
                member.gname = ""
                extractable.append(member)

        for member in whiteouts:
            path = PurePosixPath(member.name)
            parent = rootfs.joinpath(*path.parent.parts)
            if path.name == ".wh..wh..opq":
                if parent.is_dir():
                    for child in parent.iterdir():
                        _remove_path(child)
            else:
                target_name = path.name.removeprefix(".wh.")
                _remove_path(parent / target_name)

        for member in extractable:
            archive.extract(member, path=rootfs, set_attrs=True, numeric_owner=False, filter="fully_trusted")


def pull_rootfs(
    *,
    image: str,
    rootfs: Path,
    cache: Path,
    username: str,
    password: str,
) -> dict[str, Any]:
    prefix = "ghcr.io/"
    if not image.startswith(prefix) or "@sha256:" not in image:
        raise ValueError("image must be a digest-pinned ghcr.io reference")
    name, reference = image[len(prefix) :].split("@", 1)
    bearer = _registry_token(repository=name, username=username, password=password)
    manifest, resolved_digest = _select_amd64_manifest(
        repository=name,
        reference=reference,
        bearer=bearer,
    )
    if reference.startswith("sha256:") and resolved_digest != reference:
        raise RuntimeError(
            f"resolved manifest digest differs from pinned digest: {resolved_digest} != {reference}"
        )
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise RuntimeError("OCI manifest contains no layers")
    config_descriptor = manifest.get("config")
    if not isinstance(config_descriptor, dict):
        raise RuntimeError("OCI manifest contains no config descriptor")

    marker = rootfs / ".smc4-oci-manifest.json"
    if marker.exists():
        current = json.loads(marker.read_text(encoding="utf-8"))
        if current.get("manifest_digest") == resolved_digest:
            return current
        shutil.rmtree(rootfs)
    rootfs.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    config_digest = str(config_descriptor["digest"])
    config_blob = _download_blob(
        repository=name,
        digest=config_digest,
        bearer=bearer,
        destination=cache / config_digest.replace(":", "_"),
    )
    config = json.loads(config_blob.read_text(encoding="utf-8"))

    applied: list[str] = []
    for index, descriptor in enumerate(layers):
        digest = str(descriptor["digest"])
        blob = _download_blob(
            repository=name,
            digest=digest,
            bearer=bearer,
            destination=cache / digest.replace(":", "_"),
        )
        print(f"applying OCI layer {index + 1}/{len(layers)} {digest}", flush=True)
        _apply_layer(blob, rootfs)
        applied.append(digest)

    metadata = {
        "image": image,
        "repository": name,
        "manifest_digest": resolved_digest,
        "config_digest": config_digest,
        "layers": applied,
        "config": config.get("config", {}),
    }
    marker.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def _resolve_container_path(rootfs: Path, path: str) -> Path:
    current = rootfs / path.lstrip("/")
    seen: set[Path] = set()
    for _ in range(40):
        if current in seen:
            raise RuntimeError(f"symlink loop while resolving {path}")
        seen.add(current)
        if not current.is_symlink():
            return current
        target = os.readlink(current)
        if os.path.isabs(target):
            current = rootfs / target.lstrip("/")
        else:
            current = current.parent / target
        current = Path(os.path.normpath(current))
    raise RuntimeError(f"too many symlinks while resolving {path}")


def _python_command(rootfs: Path, arguments: Iterable[str]) -> list[str]:
    loader_candidates = (
        "/lib64/ld-linux-x86-64.so.2",
        "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
    )
    loader = next(
        (
            _resolve_container_path(rootfs, candidate)
            for candidate in loader_candidates
            if (rootfs / candidate.lstrip("/")).exists()
        ),
        None,
    )
    if loader is None or not loader.exists():
        raise RuntimeError("could not find the image's x86-64 dynamic loader")
    python = _resolve_container_path(rootfs, "/opt/smc4/.venv/bin/python")
    if not python.exists():
        raise RuntimeError("pinned image Python interpreter is missing")
    library_dirs = [
        rootfs / "lib/x86_64-linux-gnu",
        rootfs / "usr/lib/x86_64-linux-gnu",
        rootfs / "usr/local/lib",
        rootfs / "opt/smc4/.venv/lib",
    ]
    library_path = ":".join(str(path) for path in library_dirs if path.exists())
    return [str(loader), "--library-path", library_path, str(python), *arguments]


def run_python(*, rootfs: Path, repository: Path, arguments: list[str]) -> int:
    site_packages = rootfs / "opt/smc4/.venv/lib/python3.13/site-packages"
    stdlib = rootfs / "usr/local/lib/python3.13"
    if not site_packages.exists() or not stdlib.exists():
        raise RuntimeError("pinned Python environment is incomplete")
    env = dict(os.environ)
    env.update(
        {
            "PYTHONHOME": str(rootfs / "usr/local"),
            "PYTHONPATH": ":".join(
                (
                    str(repository / "src"),
                    str(repository / "research/candidate-03"),
                    str(site_packages),
                )
            ),
            "VIRTUAL_ENV": str(rootfs / "opt/smc4/.venv"),
            "SMC4_PREBUILT_ENV": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "SSL_CERT_FILE": str(rootfs / "etc/ssl/certs/ca-certificates.crt"),
            # Keep host ldd available for smc4 doctor; all Python and extension
            # libraries still come from the digest-pinned rootfs.
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }
    )
    command = _python_command(rootfs, arguments)
    print("pinned-python:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=repository, env=env, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    pull = subparsers.add_parser("pull")
    pull.add_argument("--image", required=True)
    pull.add_argument("--rootfs", type=Path, required=True)
    pull.add_argument("--cache", type=Path, required=True)
    pull.add_argument("--username", required=True)
    pull.add_argument("--password", required=True)

    execute = subparsers.add_parser("python")
    execute.add_argument("--rootfs", type=Path, required=True)
    execute.add_argument("--repository", type=Path, required=True)
    execute.add_argument("arguments", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    if args.command == "pull":
        metadata = pull_rootfs(
            image=args.image,
            rootfs=args.rootfs.resolve(),
            cache=args.cache.resolve(),
            username=args.username,
            password=args.password,
        )
        print(json.dumps({"manifest_digest": metadata["manifest_digest"], "layers": len(metadata["layers"])}, sort_keys=True))
        return 0
    arguments = list(args.arguments)
    if arguments and arguments[0] == "--":
        arguments.pop(0)
    if not arguments:
        parser.error("python subcommand requires interpreter arguments")
    return run_python(
        rootfs=args.rootfs.resolve(),
        repository=args.repository.resolve(),
        arguments=arguments,
    )


if __name__ == "__main__":
    raise SystemExit(main())
