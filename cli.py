"""Checkout launcher; installed entrypoint uses realms.cli to avoid Hermes' cli module."""

from realms.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
