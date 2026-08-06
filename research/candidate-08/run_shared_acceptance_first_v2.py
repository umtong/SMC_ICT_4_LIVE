"""Compatibility entrypoint; replaced only by one routed implementation correction."""

from run_shared_acceptance_first_v1 import main


if __name__ == "__main__":
    raise SystemExit(main())
