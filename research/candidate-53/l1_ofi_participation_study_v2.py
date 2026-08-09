#!/usr/bin/env python3
"""Compatibility wrapper for the frozen L1 OFI participation study."""
from __future__ import annotations

import runpy
from pathlib import Path

import bookticker_source_v3 as source

_original = source.download_verified


class _SourceProxy:
    def __init__(self, record):
        self.kind = record.kind
        self.source_url = record.source_url
        self.local_path = record.local_path
        self.sha256 = record.sha256
        self.size_bytes = record.size_bytes
        self.__dict__ = {
            "kind": record.kind,
            "source_url": record.source_url,
            "local_path": record.local_path,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
        }


def _download_verified(*args, **kwargs):
    return _SourceProxy(_original(*args, **kwargs))


source.download_verified = _download_verified
runpy.run_path(str(Path(__file__).with_name("l1_ofi_participation_study.py")), run_name="__main__")
