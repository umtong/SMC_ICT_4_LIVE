#!/usr/bin/env python3
"""Install the shared timestamp contract before running NASOSv5 scan."""
from __future__ import annotations

from timestamp_contract import install as install_timestamp_contract

install_timestamp_contract()

from nasosv5_signal_scan import main  # noqa: E402


if __name__ == "__main__":
    main()
