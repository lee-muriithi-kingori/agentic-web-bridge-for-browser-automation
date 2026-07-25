"""Tests for the webbridge client CLI (v4)."""

import json
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import client


# ===================================================================
# Helper: mock urlopen responses
# ===================================================================

def _make_response(payload, status=200):
    """Build a mock urllib response that returns *payload* as JSON."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.status = status
    return resp


# ===================================================================
# _http_post / _http_get
# ===================================================================

class TestHttpPost(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_post_sends_json(self, mock_urlopen):
        mock_urlopen.return_value = _make_response({"ok": True})
        result = client._http_post("http://127.0.0.1:9876", "/cmd", {"type": "ping"})
        self.assertTrue(result["ok"])
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.method, "POST")
        self.assertIn("/cmd", req.full_url)

    @patch("urllib.request.urlopen")
    def test_post_empty_body(self, mock_urlopen):
        mock_urlopen.return_value = _make_response({"ok": True})
        client._http_post("http://127.0.0.1:9876", "/state", {})
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.data, b"{}")

    @patch("urllib.request.urlopen")
    def test_post_connection_error_raises_bridge_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("refused")
        with self.assertRaises(client.BridgeError) as ctx:
            client._http_post("http://127.0.0.1:9876", "/cmd", {"type": "ping"})
        self.assertIn("Connection failed", str(ctx.exception))


class TestHttpGet(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_get_returns_parsed_json(self, mock_urlopen):
        mock_urlopen.return_value = _make_response({"url": "http://x"})
        result = client._http_get("http://127.0.0.1:9876", "/state")
        self.assertEqual(result["url"], "http://x")
        # GET passes the URL string directly to urlopen
        url_arg = mock_urlopen.call_args[0][0]
        self.assertIn("/state", url_arg)

    @patch("urllib.request.urlopen")
    def test_get_connection_error_raises_bridge_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        with self.assertRaises(client.BridgeError):
            client._http_get("http://127.0.0.1:9876", "/state")


# ===================================================================
# send_command
# ===================================================================

class TestSendCommand(unittest.TestCase):
    @patch("client._http_get")
    @patch("client._http_post")
    def test_send_command_posts_and_gets(self, mock_post, mock_get):
        mock_post.return_value = {"ok": True, "id": "cli-123"}
        mock_get.return_value = {"ok": True, "result": {"ok": True, "value": "42"}}
        result = client.send_command("http://127.0.0.1:9876", "eval",
                                      {"code": "6*7"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "42")
        mock_post.assert_called_once()
        mock_get.assert_called_once()
        # Verify POST body
        post_body = mock_post.call_args[0][2]
        self.assertEqual(post_body["type"], "eval")
        self.assertEqual(post_body["args"], {"code": "6*7"})

    @patch("client._http_get")
    @patch("client._http_post")
    def test_send_command_with_tab_id(self, mock_post, mock_get):
        mock_post.return_value = {"ok": True, "id": "cli-456"}
        mock_get.return_value = {"ok": True, "result": {"ok": True, "value": None}}
        client.send_command("http://127.0.0.1:9876", "ping",
                            tab_id="tab-abc")
        post_body = mock_post.call_args[0][2]
        self.assertEqual(post_body["tabId"], "tab-abc")

    @patch("client._http_get")
    @patch("client._http_post")
    def test_send_command_error_result(self, mock_post, mock_get):
        mock_post.return_value = {"ok": True, "id": "cli-err"}
        mock_get.return_value = {"ok": True, "result": {"ok": False, "error": "boom"}}
        result = client.send_command("http://127.0.0.1:9876", "eval",
                                      {"code": "bad"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "boom")

    @patch("client._http_get")
    @patch("client._http_post")
    def test_send_command_pending(self, mock_post, mock_get):
        mock_post.return_value = {"ok": True, "id": "cli-pend"}
        mock_get.return_value = {"ok": False, "pending": True}
        result = client.send_command("http://127.0.0.1:9876", "eval",
                                      {"code": "slow"})
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("pending"))


# ===================================================================
# CLI argument parsing (build_parser + main)
# ===================================================================

class TestBuildParser(unittest.TestCase):
    def test_parser_exists(self):
        p = client.build_parser()
        self.assertIsNotNone(p)

    def test_ping_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["ping"])
        self.assertEqual(args.command, "ping")

    def test_eval_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["eval", "1+2"])
        self.assertEqual(args.command, "eval")
        self.assertEqual(args.code, "1+2")

    def test_navigate_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["navigate", "https://example.com"])
        self.assertEqual(args.command, "navigate")
        self.assertEqual(args.url, "https://example.com")

    def test_click_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["click", "#btn"])
        self.assertEqual(args.command, "click")
        self.assertEqual(args.selector, "#btn")

    def test_type_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["type", "#q", "hello"])
        self.assertEqual(args.command, "type")
        self.assertEqual(args.selector, "#q")
        self.assertEqual(args.text, "hello")

    def test_key_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["key", "Enter"])
        self.assertEqual(args.command, "key")
        self.assertEqual(args.key, "Enter")

    def test_screenshot_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["screenshot"])
        self.assertEqual(args.command, "screenshot")

    def test_html_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["html"])
        self.assertEqual(args.command, "html")

    def test_url_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["url"])
        self.assertEqual(args.command, "url")

    def test_title_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["title"])
        self.assertEqual(args.command, "title")

    def test_scroll_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["scroll", "down"])
        self.assertEqual(args.command, "scroll")
        self.assertEqual(args.direction, "down")

    def test_upload_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["upload", "input[type=file]", "/a.txt", "/b.pdf"])
        self.assertEqual(args.command, "upload")
        self.assertEqual(args.files, ["/a.txt", "/b.pdf"])

    def test_trace_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["trace"])
        self.assertEqual(args.command, "trace")

    def test_cookies_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["cookies", "get"])
        self.assertEqual(args.command, "cookies")
        self.assertEqual(args.cookie_action, "get")

    def test_tabs_focus_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["tabs-focus", "tab123"])
        self.assertEqual(args.command, "tabs-focus")
        self.assertEqual(args.tab_id, "tab123")

    def test_log_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["log", "--count", "5"])
        self.assertEqual(args.command, "log")
        self.assertEqual(args.count, 5)


class TestCLIFlags(unittest.TestCase):
    def test_server_flag(self):
        p = client.build_parser()
        args = p.parse_args(["--server", "http://other:1234", "ping"])
        self.assertEqual(args.server, "http://other:1234")

    def test_tab_flag(self):
        p = client.build_parser()
        args = p.parse_args(["--tab", "tab99", "screenshot"])
        self.assertEqual(args.tab_id, "tab99")

    def test_wait_flag(self):
        p = client.build_parser()
        args = p.parse_args(["--wait", "500", "ping"])
        self.assertEqual(args.wait, 500)

    def test_json_flag(self):
        p = client.build_parser()
        args = p.parse_args(["--json", "ping"])
        self.assertTrue(args.raw_json)

    def test_quiet_flag(self):
        p = client.build_parser()
        args = p.parse_args(["--quiet", "ping"])
        self.assertTrue(args.quiet)


class TestMainDispatch(unittest.TestCase):
    @patch("client.send_command")
    def test_ping_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "pong"}
        with patch("sys.argv", ["webbridge", "ping"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        mock_sc.assert_called_once()

    @patch("client.send_command")
    def test_eval_dispatches_with_code(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": 3}
        with patch("sys.argv", ["webbridge", "eval", "1+2"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        # send_command(server, cmd_type, args, ...) — args is positional
        args = mock_sc.call_args
        self.assertEqual(args[0][1], "eval")
        self.assertEqual(args[0][2], {"code": "1+2"})

    @patch("client.send_command")
    def test_navigate_dispatches_with_url(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "navigate", "https://x.com"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        args = mock_sc.call_args
        self.assertEqual(args[0][1], "navigate")
        self.assertEqual(args[0][2], {"url": "https://x.com"})

    @patch("client.send_command")
    def test_click_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "click", "#btn"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("client.send_command")
    def test_type_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "type", "#q", "hello"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("client.send_command")
    def test_key_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "key", "Enter"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("client.send_command")
    def test_screenshot_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "/tmp/shot.png"}
        with patch("sys.argv", ["webbridge", "screenshot"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("client.send_command")
    def test_html_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "<html>"}
        with patch("sys.argv", ["webbridge", "html"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("client.send_command")
    def test_url_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "http://x.com"}
        with patch("sys.argv", ["webbridge", "url"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("client.send_command")
    def test_title_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "Title"}
        with patch("sys.argv", ["webbridge", "title"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    def test_no_command_returns_zero(self):
        """No subcommand should print help and return 0."""
        with patch("sys.argv", ["webbridge"]):
            rc = client.main()
        self.assertEqual(rc, 0)


class TestMainReturnCode(unittest.TestCase):
    @patch("client.send_command")
    def test_ok_returns_zero(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": 1}
        with patch("sys.argv", ["webbridge", "ping"]):
            self.assertEqual(client.main(), 0)

    @patch("client.send_command")
    def test_error_returns_one(self, mock_sc):
        mock_sc.return_value = {"ok": False, "error": "fail"}
        with patch("sys.argv", ["webbridge", "eval", "bad"]):
            self.assertEqual(client.main(), 1)

    @patch("client.send_command")
    def test_bridge_error_returns_one(self, mock_sc):
        mock_sc.side_effect = client.BridgeError("Connection refused")
        with patch("sys.argv", ["webbridge", "ping"]):
            self.assertEqual(client.main(), 1)


class TestMainConfigFlags(unittest.TestCase):
    @patch("client.send_command")
    def test_server_flag_passed_through(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "--server", "http://other:1234", "ping"]):
            client.main()
        server_url = mock_sc.call_args[0][0]
        self.assertEqual(server_url, "http://other:1234")

    @patch("client.send_command")
    def test_wait_flag_passed_through(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "--wait", "500", "ping"]):
            client.main()
        wait_ms = mock_sc.call_args[1]["wait_ms"]
        self.assertEqual(wait_ms, 500)

    @patch("client.send_command")
    def test_tab_flag_sets_cfg(self, mock_sc):
        """--tab is parsed and the command still executes."""
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "--tab", "tab99", "screenshot"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        mock_sc.assert_called_once()


class TestPrintResult(unittest.TestCase):
    @patch("builtins.print")
    def test_print_result_ok_value(self, mock_print):
        client._print_result({"ok": True, "value": "hello"})
        mock_print.assert_called_with("hello")

    @patch("builtins.print")
    def test_print_result_error(self, mock_print):
        client._print_result({"ok": False, "error": "boom"})
        printed = mock_print.call_args[0][0]
        self.assertIn("boom", printed)

    @patch("builtins.print")
    def test_print_result_none_value(self, mock_print):
        client._print_result({"ok": True, "value": None})
        printed = mock_print.call_args[0][0]
        self.assertIn("OK", printed)

    @patch("builtins.print")
    def test_print_result_raw_json(self, mock_print):
        client._print_result({"ok": True, "value": 42}, raw_json=True)
        printed = mock_print.call_args[0][0]
        parsed = json.loads(printed)
        self.assertEqual(parsed["value"], 42)


class TestConfigLoading(unittest.TestCase):
    def test_default_config(self):
        cfg = client._load_config()
        self.assertEqual(cfg["server"], "http://127.0.0.1:9876")
        self.assertIsNone(cfg["tab"])
        self.assertEqual(cfg["wait"], 15000)

    @patch.dict(os.environ, {"WEBBRIDGE_URL": "http://env-host:5555"})
    def test_env_overrides_server(self):
        cfg = client._load_config()
        self.assertEqual(cfg["server"], "http://env-host:5555")


if __name__ == "__main__":
    unittest.main()
