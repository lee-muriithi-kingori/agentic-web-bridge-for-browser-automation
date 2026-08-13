"""Shared fixtures for webbridge tests.

This module is usable by both pytest (via conftest.py auto-loading) and
unittest (via direct import).  It does NOT require pytest.
"""

import socket
import sys
import threading
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from webbridge.server import Bridge, Handler, Server


def free_port():
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(data_dir):
    """Start a server on a random port. Returns (bridge, server, url, thread).

    The caller is responsible for calling server.shutdown() and thread.join().
    """
    import webbridge.server as srv_mod

    port = free_port()
    bridge = Bridge(data_dir)
    old_br = getattr(srv_mod, "BRIDGE", None)
    srv_mod.BRIDGE = bridge
    srv = Server(("127.0.0.1", port), Handler)
    srv._bridge = bridge
    srv._old_br = old_br
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    return bridge, srv, url, t


def stop_server(srv, t):
    """Shut down a server started with start_server."""
    import webbridge.server as srv_mod

    srv.shutdown()
    t.join(timeout=2)
    old_br = getattr(srv, "_old_br", None)
    if old_br is None and hasattr(srv_mod, "BRIDGE"):
        delattr(srv_mod, "BRIDGE")
    else:
        srv_mod.BRIDGE = old_br
