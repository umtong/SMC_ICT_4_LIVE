"""Materialize the prospectively locked candidate-02 v3 bundle."""

from __future__ import annotations

import base64
from hashlib import sha256
from pathlib import Path
import tarfile


EXPECTED_SHA256 = "579b455a1b95427e1954d836be83c0ae9ab14d190b9edef0096c7de5245d93b8"
BOOTSTRAP = Path(".candidate-02-bootstrap")
parts = sorted(BOOTSTRAP.glob("v3-part-*"))
if not parts:
    raise RuntimeError("candidate-02 v3 bundle parts are missing")
encoded = "".join(path.read_text(encoding="ascii") for path in parts)
payload = base64.b64decode(encoded, validate=True)
actual = sha256(payload).hexdigest()
if actual != EXPECTED_SHA256:
    raise RuntimeError(f"candidate-02 v3 bundle hash mismatch: {actual}")

archive_path = Path("/tmp/candidate-02-v3-lock.tar.gz")
archive_path.write_bytes(payload)
root = Path.cwd().resolve()
with tarfile.open(archive_path, "r:gz") as archive:
    for member in archive.getmembers():
        if not member.name.startswith("research/candidate-02/"):
            raise RuntimeError(f"unexpected bundle member: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise RuntimeError(f"unsupported bundle member type: {member.name}")
        destination = (root / member.name).resolve()
        if destination != root and root not in destination.parents:
            raise RuntimeError(f"unsafe bundle member: {member.name}")
    archive.extractall(root, filter="data")
