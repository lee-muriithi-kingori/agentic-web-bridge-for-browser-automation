#!/usr/bin/env python3
"""Standalone entry point — re-exports from the webbridge package.

This file exists so users can still run `python server.py` from a fresh
checkout without installing the package, AND so existing tests/scripts that
do `from server import Bridge, Handler, Server` keep working. The canonical
implementation lives in webbridge/server.py.
"""
from webbridge.server import (  # noqa: F401
    Bridge, Handler, Server, BRIDGE, COMMAND_TYPES, RESULT_TTL, LOG_MAX,
    main, _do_shutdown, _signal_handler, parse_args, setup_logging,
)
from webbridge._version import __version__  # noqa: F401

if __name__ == "__main__":
    main()
