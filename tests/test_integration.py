"""Integration tests — require a running webbridge server.

These are skipped by default when the server is not reachable.
Run with a live server:  python -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import client

# Detect whether the default server is up
_SERVER_UP = False
try:
    with urllib.request.urlopen("http://127.0.0.1:9876/", timeout=2) as _r:
        _SERVER_UP = True
except Exception:
    pass

_SKIP_REASON = "webbridge server not running on 127.0.0.1:9876"


@unittest.skipUnless(_SERVER_UP, _SKIP_REASON)
class TestPingRoundTrip(unittest.TestCase):
    def test_ping_via_send_command(self):
        result = client.send_command("http://127.0.0.1:9876", "ping")
        self.assertTrue(result.get("ok"))

    def test_ping_via_raw_http(self):
        data = json.dumps({"type": "ping", "args": {}}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:9876/cmd",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
        self.assertTrue(body.get("ok"))
        cid = body["id"]
        with urllib.request.urlopen(
            f"http://127.0.0.1:9876/result?id={cid}&wait=5000", timeout=10
        ) as r:
            res = json.loads(r.read())
        self.assertTrue(res.get("ok"))


@unittest.skipUnless(_SERVER_UP, _SKIP_REASON)
class TestNavigateRoundTrip(unittest.TestCase):
    def test_navigate_and_get_title(self):
        result = client.send_command(
            "http://127.0.0.1:9876", "navigate",
            args={"url": "https://example.com"},
        )
        self.assertTrue(result.get("ok"))
        title_result = client.send_command(
            "http://127.0.0.1:9876", "title",
        )
        self.assertTrue(title_result.get("ok"))
        self.assertIn("Example", title_result.get("value", ""))


@unittest.skipUnless(_SERVER_UP, _SKIP_REASON)
class TestEvalRoundTrip(unittest.TestCase):
    def test_eval_simple_expression(self):
        result = client.send_command(
            "http://127.0.0.1:9876", "eval",
            args={"code": "1 + 2"},
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("value"), 3)

    def test_eval_document_title(self):
        client.send_command(
            "http://127.0.0.1:9876", "navigate",
            args={"url": "https://example.com"},
        )
        result = client.send_command(
            "http://127.0.0.1:9876", "eval",
            args={"code": "document.title"},
        )
        self.assertTrue(result.get("ok"))
        self.assertIn("Example", result.get("value", ""))


@unittest.skipUnless(_SERVER_UP, _SKIP_REASON)
class TestScreenshotRoundTrip(unittest.TestCase):
    def test_screenshot_saves_file(self):
        result = client.send_command(
            "http://127.0.0.1:9876", "screenshot",
        )
        self.assertTrue(result.get("ok"))
        path = result.get("value")
        self.assertIsNotNone(path)
        self.assertTrue(os.path.isfile(path), f"Screenshot file not found: {path}")
        self.assertGreater(os.path.getsize(path), 0)


if __name__ == "__main__":
    unittest.main()
