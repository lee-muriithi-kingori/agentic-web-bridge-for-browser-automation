"""Tests for the webbridge server (v4) — Bridge class and HTTP Handler."""

import http.client
import json
import tempfile
import os
import socket
import threading
import time
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import webbridge.server as srv_mod
from webbridge.server import Bridge, Handler, Server, RESULT_TTL, COMMAND_TYPES, BRIDGE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tmp_data_dir():
    """Return a fresh temp directory path. Cross-platform (no hardcoded /tmp)."""
    return tempfile.mkdtemp(prefix="wb_test_")




def _start_server(data_dir):
    """Start a server on a random port. Returns (bridge, server, url, thread)."""
    import webbridge.server as srv_mod

    port = _free_port()
    bridge = Bridge(data_dir)
    old_br = getattr(srv_mod, "BRIDGE", None)
    srv_mod.BRIDGE = bridge
    srv = Server(("127.0.0.1", port), Handler)
    srv._old_br = old_br
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    return bridge, srv, url, t


def _stop_server(srv, t):
    import webbridge.server as srv_mod

    srv.shutdown()
    t.join(timeout=2)
    old_br = getattr(srv, "_old_br", None)
    if old_br is None and hasattr(srv_mod, "BRIDGE"):
        delattr(srv_mod, "BRIDGE")
    else:
        srv_mod.BRIDGE = old_br


def _get(url, path, timeout=30):
    host, port = url.split("://")[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=timeout)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = json.loads(resp.read().decode())
    conn.close()
    return resp.status, body


def _post(url, path, data=None, timeout=30):
    host, port = url.split("://")[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=timeout)
    body_bytes = json.dumps(data or {}).encode()
    conn.request("POST", path, body=body_bytes,
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = json.loads(resp.read().decode())
    conn.close()
    return resp.status, body


def _with_server(fn):
    """Run fn(bridge, url) against a temporary server."""
    bridge, srv, url, t = _start_server(_tmp_data_dir())
    try:
        fn(bridge, url)
    finally:
        _stop_server(srv, t)


# ===================================================================
# Bridge unit tests (no server needed)
# ===================================================================

class TestBridgeInit(unittest.TestCase):
    def test_initial_state_has_expected_keys(self):
        b = Bridge(_tmp_data_dir())
        for key in ("url", "title", "tabId", "extId", "snippet", "ts"):
            self.assertIn(key, b.state)

    def test_cmd_queue_starts_empty(self):
        b = Bridge(_tmp_data_dir())
        self.assertEqual(len(b.cmd_queue), 0)

    def test_results_starts_empty(self):
        b = Bridge(_tmp_data_dir())
        self.assertEqual(len(b.results), 0)

    def test_log_lines_starts_empty(self):
        b = Bridge(_tmp_data_dir())
        self.assertEqual(len(b.log_lines), 0)

    def test_data_dir_stored(self):
        b = Bridge(_tmp_data_dir())
        self.assertTrue(b.data_dir)

    def test_screenshot_dir_derived(self):
        b = Bridge(_tmp_data_dir())
        self.assertEqual(b.screenshot_dir, os.path.join(b.data_dir, "screenshots"))

    def test_trace_root_derived(self):
        b = Bridge(_tmp_data_dir())
        self.assertEqual(b.trace_root, os.path.join(b.data_dir, "traces"))


class TestBridgeEnqueue(unittest.TestCase):
    def test_enqueue_returns_true(self):
        b = Bridge(_tmp_data_dir())
        result = b.enqueue({"id": "c1", "type": "ping", "args": {}})
        self.assertTrue(result)

    def test_enqueue_adds_to_queue(self):
        b = Bridge(_tmp_data_dir())
        cmd = {"id": "c2", "type": "eval", "args": {"code": "1+1"}}
        b.enqueue(cmd)
        self.assertEqual(len(b.cmd_queue), 1)
        self.assertEqual(b.cmd_queue[0], cmd)

    def test_enqueue_multiple_commands_fifo(self):
        b = Bridge(_tmp_data_dir())
        b.enqueue({"id": "c1", "type": "ping", "args": {}})
        b.enqueue({"id": "c2", "type": "html", "args": {}})
        self.assertEqual(b.cmd_queue[0]["id"], "c1")
        self.assertEqual(b.cmd_queue[1]["id"], "c2")


class TestBridgeDequeue(unittest.TestCase):
    def test_dequeue_empty_returns_none(self):
        b = Bridge(_tmp_data_dir())
        self.assertIsNone(b.dequeue("ext1"))

    def test_dequeue_returns_command(self):
        b = Bridge(_tmp_data_dir())
        cmd = {"id": "c1", "type": "navigate", "args": {"url": "http://x"}}
        b.enqueue(cmd)
        got = b.dequeue("ext1")
        self.assertEqual(got, cmd)

    def test_dequeue_sets_ext_id(self):
        b = Bridge(_tmp_data_dir())
        b.enqueue({"id": "c1", "type": "ping", "args": {}})
        b.dequeue("my-extension")
        self.assertEqual(b.state["extId"], "my-extension")

    def test_dequeue_fifo_order(self):
        b = Bridge(_tmp_data_dir())
        c1 = {"id": "c1", "type": "ping", "args": {}}
        c2 = {"id": "c2", "type": "html", "args": {}}
        b.enqueue(c1)
        b.enqueue(c2)
        self.assertEqual(b.dequeue("ext")["id"], "c1")
        self.assertEqual(b.dequeue("ext")["id"], "c2")
        self.assertIsNone(b.dequeue("ext"))


class TestBridgePostResult(unittest.TestCase):
    def test_post_result_stores(self):
        b = Bridge(_tmp_data_dir())
        b.post_result("r1", True, value="hello")
        self.assertIn("r1", b.results)
        self.assertTrue(b.results["r1"]["ok"])
        self.assertEqual(b.results["r1"]["value"], "hello")

    def test_post_result_with_error(self):
        b = Bridge(_tmp_data_dir())
        b.post_result("r2", False, error="not found")
        self.assertFalse(b.results["r2"]["ok"])
        self.assertEqual(b.results["r2"]["error"], "not found")

    def test_post_result_has_timestamp(self):
        b = Bridge(_tmp_data_dir())
        before = time.time()
        b.post_result("r3", True, value=42)
        after = time.time()
        self.assertGreaterEqual(b.results["r3"]["ts"], before)
        self.assertLessEqual(b.results["r3"]["ts"], after)


class TestBridgeWaitForResult(unittest.TestCase):
    def test_wait_returns_none_on_timeout(self):
        b = Bridge(_tmp_data_dir())
        result = b.wait_for_result("nonexistent", wait_ms=50)
        self.assertIsNone(result)

    def test_wait_returns_result_when_available(self):
        b = Bridge(_tmp_data_dir())
        def post():
            time.sleep(0.05)
            b.post_result("r1", True, value="done")
        threading.Thread(target=post, daemon=True).start()
        result = b.wait_for_result("r1", wait_ms=2000)
        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "done")

    def test_wait_returns_result_already_present(self):
        b = Bridge(_tmp_data_dir())
        b.post_result("r1", True, value="already here")
        result = b.wait_for_result("r1", wait_ms=100)
        self.assertIsNotNone(result)
        self.assertEqual(result["value"], "already here")


class TestBridgeState(unittest.TestCase):
    def test_get_state_returns_dict(self):
        b = Bridge(_tmp_data_dir())
        state = b.get_state()
        self.assertIsInstance(state, dict)
        self.assertEqual(state["url"], None)

    def test_post_state_updates(self):
        b = Bridge(_tmp_data_dir())
        b.post_state("ext1", {"url": "http://example.com", "title": "Example"})
        state = b.get_state()
        self.assertEqual(state["url"], "http://example.com")
        self.assertEqual(state["title"], "Example")
        self.assertEqual(state["extId"], "ext1")

    def test_get_state_returns_copy(self):
        b = Bridge(_tmp_data_dir())
        state = b.get_state()
        state["url"] = "tampered"
        self.assertIsNone(b.get_state()["url"])

    def test_state_round_trip(self):
        b = Bridge(_tmp_data_dir())
        payload = {"url": "https://test.dev", "title": "Test", "tabId": 42}
        b.post_state("ext99", payload)
        got = b.get_state()
        self.assertEqual(got["url"], "https://test.dev")
        self.assertEqual(got["title"], "Test")
        self.assertEqual(got["tabId"], 42)
        self.assertEqual(got["extId"], "ext99")
        self.assertGreater(got["ts"], 0)


class TestBridgeLog(unittest.TestCase):
    def test_log_adds_line(self):
        b = Bridge(_tmp_data_dir())
        b.log("test", "hello world")
        self.assertEqual(len(b.log_lines), 1)
        self.assertIn("hello world", b.log_lines[0])

    def test_tail_log_returns_last_n(self):
        b = Bridge(_tmp_data_dir())
        for i in range(10):
            b.log("t", f"line {i}")
        tail = b.tail_log(3)
        self.assertEqual(len(tail), 3)
        self.assertIn("line 9", tail[-1])


class TestBridgeGC(unittest.TestCase):
    def test_gc_removes_stale_results(self):
        b = Bridge(_tmp_data_dir())
        b.results["old"] = {"ok": True, "value": "x", "error": None,
                             "ts": time.time() - RESULT_TTL - 10}
        b.results["new"] = {"ok": True, "value": "y", "error": None,
                             "ts": time.time()}
        b.gc_results()
        self.assertNotIn("old", b.results)
        self.assertIn("new", b.results)

    def test_gc_keeps_fresh_results(self):
        b = Bridge(_tmp_data_dir())
        b.results["fresh"] = {"ok": True, "value": "y", "error": None,
                               "ts": time.time()}
        b.gc_results()
        self.assertIn("fresh", b.results)


class TestBridgeShutdown(unittest.TestCase):
    def test_shutdown_sets_flag(self):
        b = Bridge(_tmp_data_dir())
        self.assertFalse(b.is_shutting_down)
        b.shutdown()
        self.assertTrue(b.is_shutting_down)


class TestCommandTypes(unittest.TestCase):
    def test_command_types_list_non_empty(self):
        self.assertIsInstance(COMMAND_TYPES, list)
        self.assertGreater(len(COMMAND_TYPES), 0)

    def test_expected_commands_present(self):
        expected = {"ping", "eval", "navigate", "click", "type", "html",
                     "url", "title", "screenshot", "tabs", "active_tab",
                     "attach", "detach", "reload", "see", "axtree",
                     "axquery", "expect", "move", "trace"}
        self.assertTrue(expected.issubset(set(COMMAND_TYPES)))


# ===================================================================
# HTTP Handler tests (real HTTP to a temporary server)
# ===================================================================

class TestHandlerHealth(unittest.TestCase):
    def test_get_root(self):
        def check(b, url):
            status, body = _get(url, "/")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["service"], "webbridge")
            # Version is now a single source of truth from webbridge._version
            self.assertTrue(body["version"].startswith("4."))
            # New in v4: pyautogui availability flag and /os, /version endpoints
            self.assertIn("pyautogui", body)
            self.assertIn("/os", body["endpoints"])
            self.assertIn("/version", body["endpoints"])
            # v4.1: auth_enabled flag
            self.assertIn("auth_enabled", body)
            self.assertIsInstance(body["auth_enabled"], bool)
        _with_server(check)

    def test_get_health(self):
        def check(b, url):
            status, body = _get(url, "/health")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
        _with_server(check)


class TestHandlerCommandsEndpoint(unittest.TestCase):
    def test_commands_returns_list(self):
        def check(b, url):
            status, body = _get(url, "/commands")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertIn("commands", body)
            self.assertIn("ping", body["commands"])
        _with_server(check)


class TestHandlerStateEndpoint(unittest.TestCase):
    def test_get_state_returns_empty(self):
        def check(b, url):
            status, body = _get(url, "/state")
            self.assertEqual(status, 200)
            self.assertIn("url", body)
        _with_server(check)

    def test_post_state_and_get(self):
        def check(b, url):
            _post(url, "/state", {"ext": "ext1", "url": "http://x.com"})
            status, body = _get(url, "/state")
            self.assertEqual(status, 200)
            self.assertEqual(body["url"], "http://x.com")
        _with_server(check)


class TestHandlerPollEndpoint(unittest.TestCase):
    def test_poll_empty_returns_none(self):
        def check(b, url):
            # Pass ?wait=0 so the long-poll returns immediately (no blocking).
            status, body = _get(url, "/poll?ext=e1&wait=0")
            self.assertEqual(status, 200)
            self.assertIsNone(body.get("id"))
        _with_server(check)

    def test_poll_returns_enqueued(self):
        b_pre, srv, url, t = _start_server(_tmp_data_dir())
        try:
            b_pre.enqueue({"id": "p1", "type": "ping", "args": {}})
            status, body = _get(url, "/poll?ext=e1&wait=0")
            self.assertEqual(status, 200)
            self.assertEqual(body.get("id"), "p1")
            self.assertEqual(body.get("type"), "ping")
        finally:
            _stop_server(srv, t)

    def test_poll_long_poll_blocks_until_command(self):
        """Long-poll: /poll should block until a command is enqueued."""
        b, srv, url, t = _start_server(_tmp_data_dir())
        try:
            import threading as _th
            result = {"status": None, "body": None}
            def poller():
                status, body = _get(url, "/poll?ext=e1&wait=3000")
                result["status"] = status
                result["body"] = body
            th = _th.Thread(target=poller, daemon=True)
            th.start()
            time.sleep(0.2)  # let the poller connect + start blocking
            # Now enqueue a command — the blocked poll should return immediately.
            t0 = time.time()
            b.enqueue({"id": "lp1", "type": "ping", "args": {}})
            th.join(timeout=2)
            elapsed = time.time() - t0
            self.assertEqual(result["status"], 200)
            self.assertEqual(result["body"].get("id"), "lp1")
            # Should return well under the 3s timeout since we enqueued promptly.
            self.assertLess(elapsed, 1.5)
        finally:
            _stop_server(srv, t)

    def test_poll_returns_enqueued_via_with_server(self):
        def check(b, url):
            b.enqueue({"id": "c1", "type": "ping", "args": {}})
            status, body = _get(url, "/poll?ext=e1&wait=0")
            self.assertEqual(status, 200)
            self.assertEqual(body["id"], "c1")
            self.assertEqual(body["type"], "ping")
        _with_server(check)


class TestHandlerCmdEndpoint(unittest.TestCase):
    def test_post_cmd_ok(self):
        def check(b, url):
            status, body = _post(url, "/cmd", {"type": "eval", "args": {"code": "1"}})
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertIn("id", body)
            self.assertEqual(len(b.cmd_queue), 1)
        _with_server(check)

    def test_post_cmd_without_type_returns_400(self):
        def check(b, url):
            status, body = _post(url, "/cmd", {"args": {}})
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
            self.assertIn("error", body)
        _with_server(check)

    def test_post_cmd_unknown_type_returns_400(self):
        def check(b, url):
            status, body = _post(url, "/cmd", {"type": "frobnicate"})
            self.assertEqual(status, 400)
            self.assertIn("unknown command type", body["error"])
        _with_server(check)

    def test_post_cmd_generates_id_if_missing(self):
        def check(b, url):
            status, body = _post(url, "/cmd", {"type": "ping"})
            self.assertEqual(status, 200)
            self.assertIsInstance(body["id"], str)
            self.assertTrue(len(body["id"]) > 0)
        _with_server(check)

    def test_post_cmd_with_explicit_id(self):
        def check(b, url):
            status, body = _post(url, "/cmd", {"id": "my-id", "type": "ping"})
            self.assertEqual(status, 200)
            self.assertEqual(body["id"], "my-id")
        _with_server(check)

    def test_post_cmd_invalid_json_returns_400(self):
        def check(b, url):
            host, port = url.split("://")[1].split(":")
            conn = http.client.HTTPConnection(host, int(port), timeout=5)
            conn.request("POST", "/cmd", body=b"{{bad",
                         headers={"Content-Type": "application/json",
                                  "Content-Length": "5"})
            resp = conn.getresponse()
            body = json.loads(resp.read().decode())
            conn.close()
            self.assertEqual(resp.status, 400)
            self.assertFalse(body["ok"])
            self.assertIn("error", body)
        _with_server(check)


class TestHandlerResultEndpoint(unittest.TestCase):
    def test_get_result_missing_id_returns_400(self):
        def check(b, url):
            status, body = _get(url, "/result")
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
        _with_server(check)

    def test_get_result_pending(self):
        def check(b, url):
            status, body = _get(url, "/result?id=nonexistent&wait=10")
            self.assertEqual(status, 200)
            self.assertTrue(body.get("pending"))
        _with_server(check)

    def test_post_result_and_get(self):
        def check(b, url):
            _post(url, "/result", {"id": "r1", "ok": True, "value": "hi"})
            status, body = _get(url, "/result?id=r1&wait=100")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["result"]["value"], "hi")
        _with_server(check)

    def test_post_result_without_id_returns_400(self):
        def check(b, url):
            status, body = _post(url, "/result", {"ok": True, "value": "x"})
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
        _with_server(check)


class TestHandlerLogEndpoint(unittest.TestCase):
    def test_log_returns_lines(self):
        def check(b, url):
            b.log("test", "msg1")
            status, body = _get(url, "/log?tail=10")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(len(body["lines"]), 1)
        _with_server(check)


class TestHandlerCORSEndpoint(unittest.TestCase):
    def test_cors_headers_present(self):
        def check(b, url):
            host, port = url.split("://")[1].split(":")
            conn = http.client.HTTPConnection(host, int(port), timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            self.assertEqual(headers.get("access-control-allow-origin"), "*")
            self.assertIn("access-control-allow-methods", headers)
            self.assertIn("access-control-allow-headers", headers)
            resp.read()
            conn.close()
        _with_server(check)


class TestHandlerNotFound(unittest.TestCase):
    def test_unknown_path_returns_404(self):
        def check(b, url):
            status, body = _get(url, "/nonexistent")
            self.assertEqual(status, 404)
            self.assertFalse(body["ok"])
        _with_server(check)


class TestHandlerOptions(unittest.TestCase):
    def test_options_returns_200(self):
        def check(b, url):
            host, port = url.split("://")[1].split(":")
            conn = http.client.HTTPConnection(host, int(port), timeout=5)
            conn.request("OPTIONS", "/cmd")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            resp.read()
            conn.close()
        _with_server(check)


# ===================================================================
# Concurrency / blocking tests
# ===================================================================

class TestWaitBlocking(unittest.TestCase):
    def test_wait_blocks_until_result_posted(self):
        b = Bridge(_tmp_data_dir())
        results = []

        def waiter():
            r = b.wait_for_result("blocking", wait_ms=5000)
            results.append(r)

        def poster():
            time.sleep(0.1)
            b.post_result("blocking", True, value="ready")

        t1 = threading.Thread(target=waiter)
        t2 = threading.Thread(target=poster)
        t1.start()
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(results[0]["value"], "ready")

    def test_concurrent_enqueue_dequeue(self):
        b = Bridge(_tmp_data_dir())
        for i in range(20):
            b.enqueue({"id": f"c{i}", "type": "ping", "args": {}})
        dequeued = []
        while True:
            cmd = b.dequeue("ext")
            if cmd is None:
                break
            dequeued.append(cmd)
        self.assertEqual(len(dequeued), 20)
        self.assertEqual(dequeued[0]["id"], "c0")
        self.assertEqual(dequeued[-1]["id"], "c19")


# ===================================================================
# v4 additions: readable / vision command types, /os endpoint, /version
# ===================================================================

class TestV4CommandTypes(unittest.TestCase):
    def test_readable_in_command_types(self):
        self.assertIn("readable", COMMAND_TYPES)

    def test_vision_in_command_types(self):
        self.assertIn("vision", COMMAND_TYPES)

    def test_readable_command_round_trips(self):
        """`readable` should enqueue like any other command type."""
        b = Bridge(_tmp_data_dir())
        b.enqueue({"id": "r1", "type": "readable", "args": {"maxChars": 1000}})
        cmd = b.dequeue("ext")
        self.assertEqual(cmd["type"], "readable")
        self.assertEqual(cmd["args"]["maxChars"], 1000)

    def test_vision_command_round_trips(self):
        b = Bridge(_tmp_data_dir())
        b.enqueue({"id": "v1", "type": "vision", "args": {"prompt": "describe"}})
        cmd = b.dequeue("ext")
        self.assertEqual(cmd["type"], "vision")
        self.assertEqual(cmd["args"]["prompt"], "describe")


class TestVersionEndpoint(unittest.TestCase):
    def test_get_version(self):
        def check(b, url):
            status, body = _get(url, "/version")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertTrue(body["package"].startswith("4."))
            self.assertEqual(body["extension"], "2.0.0")
            # pyautogui_available is a bool, present either way
            self.assertIsInstance(body["pyautogui_available"], bool)
        _with_server(check)


class TestOSEndpoint(unittest.TestCase):
    """Tests for POST /os — pyautogui hybrid mode.

    These tests don't require pyautogui to be installed; they verify the
    endpoint correctly reports the missing-dependency case and rejects
    unknown actions.
    """

    def _post(self, url, path, payload):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", url.split(":")[2])
        c.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        r = c.getresponse()
        body = json.loads(r.read().decode("utf-8"))
        c.close()
        return r.status, body

    def test_os_missing_action(self):
        def check(b, url):
            status, body = self._post(url, "/os", {})
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
            self.assertIn("action", body["error"])
        _with_server(check)

    def test_os_unknown_action_rejected(self):
        def check(b, url):
            status, body = self._post(url, "/os", {"action": "hackTheGibson", "args": {}})
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
            self.assertIn("allowlist", body["error"])
        _with_server(check)

    def test_os_allowlist_includes_common_actions(self):
        # Don't actually call pyautogui — just verify the allowlist logic
        # by sending an action that's in the allowlist and checking that
        # the server doesn't reject it as "unknown" (it'll fail later
        # because pyautogui isn't installed, but that's a 500 not a 400).
        def check(b, url):
            status, body = self._post(url, "/os", {"action": "click", "args": {"x": 0, "y": 0}})
            # Either 500 (pyautogui not installed) or 200 (it is). Either way
            # we should NOT get a 400 "not in allowlist" error.
            self.assertNotEqual(status, 400)
        _with_server(check)


# ===================================================================
# v4.1 additions: auth (WEBBRIDGE_TOKEN), long-poll, cross-platform paths
# ===================================================================

class TestAuthEnabled(unittest.TestCase):
    """When WEBBRIDGE_TOKEN is set, non-public endpoints require it."""

    def setUp(self):
        # Save and set the token env var for the duration of these tests.
        self._old = os.environ.get("WEBBRIDGE_TOKEN")
        os.environ["WEBBRIDGE_TOKEN"] = "test-secret-token"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("WEBBRIDGE_TOKEN", None)
        else:
            os.environ["WEBBRIDGE_TOKEN"] = self._old

    def test_health_is_public_no_token_needed(self):
        def check(b, url):
            status, body = _get(url, "/health")
            self.assertEqual(status, 200)
            self.assertTrue(body["auth_enabled"])
        _with_server(check)

    def test_version_is_public_no_token_needed(self):
        def check(b, url):
            status, body = _get(url, "/version")
            self.assertEqual(status, 200)
        _with_server(check)

    def test_state_requires_token(self):
        def check(b, url):
            # No token → 401
            status, body = _get(url, "/state")
            self.assertEqual(status, 401)
            self.assertFalse(body["ok"])
            self.assertIn("unauthorized", body["error"])
        _with_server(check)

    def test_state_with_correct_token_succeeds(self):
        def check(b, url):
            # Bearer token → 200
            import http.client as hc
            host, port = url.split("://")[1].split(":")
            conn = hc.HTTPConnection(host, int(port), timeout=5)
            conn.request("GET", "/state", headers={"Authorization": "Bearer test-secret-token"})
            resp = conn.getresponse()
            body = json.loads(resp.read().decode())
            conn.close()
            self.assertEqual(resp.status, 200)
        _with_server(check)

    def test_state_with_wrong_token_rejected(self):
        def check(b, url):
            import http.client as hc
            host, port = url.split("://")[1].split(":")
            conn = hc.HTTPConnection(host, int(port), timeout=5)
            conn.request("GET", "/state", headers={"Authorization": "Bearer wrong-token"})
            resp = conn.getresponse()
            conn.close()
            self.assertEqual(resp.status, 401)
        _with_server(check)

    def test_token_via_query_param_works(self):
        """The ?token= query param is the extension's auth path."""
        def check(b, url):
            status, body = _get(url, "/state?token=test-secret-token")
            self.assertEqual(status, 200)
        _with_server(check)

    def test_cmd_requires_token(self):
        def check(b, url):
            status, body = _post(url, "/cmd", {"type": "ping"})
            self.assertEqual(status, 401)
        _with_server(check)


class TestAuthDisabled(unittest.TestCase):
    """When WEBBRIDGE_TOKEN is NOT set, all endpoints are open (backwards-compat)."""

    def setUp(self):
        self._old = os.environ.pop("WEBBRIDGE_TOKEN", None)

    def tearDown(self):
        if self._old is not None:
            os.environ["WEBBRIDGE_TOKEN"] = self._old

    def test_state_no_token_needed_when_auth_disabled(self):
        def check(b, url):
            status, body = _get(url, "/state")
            self.assertEqual(status, 200)
        _with_server(check)


class TestCrossPlatformPaths(unittest.TestCase):
    """Verify no hardcoded /tmp paths leak into the server runtime."""

    def test_default_data_dir_is_absolute(self):
        from webbridge.server import _default_data_dir
        d = _default_data_dir()
        self.assertTrue(os.path.isabs(d), f"default data_dir should be absolute, got: {d}")

    def test_file_url_uses_forward_slashes(self):
        from webbridge.server import _file_url
        # On any OS, a file:// URL should only contain forward slashes.
        url = _file_url(os.path.join("C:", "Users", "foo", "bar.png") if sys.platform == "win32" else "/home/foo/bar.png")
        self.assertTrue(url.startswith("file://"))
        self.assertNotIn("\\", url)


if __name__ == "__main__":
    unittest.main()
