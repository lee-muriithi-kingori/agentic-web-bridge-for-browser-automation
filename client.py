#!/usr/bin/env python3
"""Standalone entry point — re-exports from the webbridge package.

This file exists so users can still run `python client.py` from a fresh
checkout without installing the package, AND so existing tests/scripts that
do `from client import ...` keep working. The canonical implementation
lives in webbridge/client.py.
"""
from webbridge.client import (  # noqa: F401
    BridgeError, send_command, build_parser, main,
    _load_config, _http_get, _http_post, _print_result,
)

if __name__ == "__main__":
    main()
