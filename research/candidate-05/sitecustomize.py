"""Candidate 05 process-wide bounded retry for observational archive downloads.

Python imports ``sitecustomize`` automatically from the candidate PYTHONPATH.
The contract changes only transport reliability: requested URLs and bytes are
unchanged, partial files are never promoted into the cache, and checksum
verification remains mandatory in the ingestion modules.
"""
from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any
import urllib.request


_ATTEMPTS = 5
_MAX_DELAY_SECONDS = 8.0


def _install_retrying_urlretrieve() -> None:
    current = urllib.request.urlretrieve
    if getattr(current, "_candidate05_bounded_retry", False):
        return

    original = current

    def retrying_urlretrieve(
        url: str,
        filename: str | os.PathLike[str] | None = None,
        reporthook: Any | None = None,
        data: bytes | None = None,
    ):
        last_error: BaseException | None = None
        destination = None if filename is None else Path(filename)
        temporary = None if destination is None else destination.with_name(
            f".{destination.name}.candidate05-part-{os.getpid()}",
        )

        for attempt in range(1, _ATTEMPTS + 1):
            try:
                if temporary is not None:
                    temporary.parent.mkdir(parents=True, exist_ok=True)
                    temporary.unlink(missing_ok=True)
                    downloaded, headers = original(
                        url,
                        str(temporary),
                        reporthook=reporthook,
                        data=data,
                    )
                    os.replace(temporary, destination)
                    return str(destination), headers
                return original(url, filename, reporthook=reporthook, data=data)
            except BaseException as exc:
                last_error = exc
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                if attempt >= _ATTEMPTS:
                    break
                time.sleep(min(2.0 ** (attempt - 1), _MAX_DELAY_SECONDS))

        assert last_error is not None
        raise last_error

    setattr(retrying_urlretrieve, "_candidate05_bounded_retry", True)
    setattr(retrying_urlretrieve, "_candidate05_original", original)
    urllib.request.urlretrieve = retrying_urlretrieve


_install_retrying_urlretrieve()
