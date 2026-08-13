"""Tests for the webbridge client CLI (v4)."""

import json
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import webbridge.client as client


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
    @patch("webbridge.client._http_get")
    @patch("webbridge.client._http_post")
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

    @patch("webbridge.client._http_get")
    @patch("webbridge.client._http_post")
    def test_send_command_with_tab_id(self, mock_post, mock_get):
        mock_post.return_value = {"ok": True, "id": "cli-456"}
        mock_get.return_value = {"ok": True, "result": {"ok": True, "value": None}}
        client.send_command("http://127.0.0.1:9876", "ping",
                            tab_id="tab-abc")
        post_body = mock_post.call_args[0][2]
        self.assertEqual(post_body["tabId"], "tab-abc")

    @patch("webbridge.client._http_get")
    @patch("webbridge.client._http_post")
    def test_send_command_error_result(self, mock_post, mock_get):
        mock_post.return_value = {"ok": True, "id": "cli-err"}
        mock_get.return_value = {"ok": True, "result": {"ok": False, "error": "boom"}}
        result = client.send_command("http://127.0.0.1:9876", "eval",
                                      {"code": "bad"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "boom")

    @patch("webbridge.client._http_get")
    @patch("webbridge.client._http_post")
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

    def test_tabs_focus_subcommand_removed(self):
        # tabs-focus was removed in v4 — the bridge is now pinned to ONE tab
        # via the popup, so per-command tab targeting is no longer needed.
        p = client.build_parser()
        with self.assertRaises(SystemExit):
            p.parse_args(["tabs-focus", "tab123"])

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
    @patch("webbridge.client.send_command")
    def test_ping_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "pong"}
        with patch("sys.argv", ["webbridge", "ping"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        mock_sc.assert_called_once()

    @patch("webbridge.client.send_command")
    def test_eval_dispatches_with_code(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": 3}
        with patch("sys.argv", ["webbridge", "eval", "1+2"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        # send_command(server, cmd_type, args, ...) — args is positional
        args = mock_sc.call_args
        self.assertEqual(args[0][1], "eval")
        self.assertEqual(args[0][2], {"code": "1+2"})

    @patch("webbridge.client.send_command")
    def test_navigate_dispatches_with_url(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "navigate", "https://x.com"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        args = mock_sc.call_args
        self.assertEqual(args[0][1], "navigate")
        self.assertEqual(args[0][2], {"url": "https://x.com"})

    @patch("webbridge.client.send_command")
    def test_click_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "click", "#btn"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("webbridge.client.send_command")
    def test_type_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "type", "#q", "hello"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("webbridge.client.send_command")
    def test_key_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "key", "Enter"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("webbridge.client.send_command")
    def test_screenshot_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "/tmp/shot.png"}
        with patch("sys.argv", ["webbridge", "screenshot"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("webbridge.client.send_command")
    def test_html_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "<html>"}
        with patch("sys.argv", ["webbridge", "html"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("webbridge.client.send_command")
    def test_url_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": "http://x.com"}
        with patch("sys.argv", ["webbridge", "url"]):
            rc = client.main()
        self.assertEqual(rc, 0)

    @patch("webbridge.client.send_command")
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
    @patch("webbridge.client.send_command")
    def test_ok_returns_zero(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": 1}
        with patch("sys.argv", ["webbridge", "ping"]):
            self.assertEqual(client.main(), 0)

    @patch("webbridge.client.send_command")
    def test_error_returns_one(self, mock_sc):
        mock_sc.return_value = {"ok": False, "error": "fail"}
        with patch("sys.argv", ["webbridge", "eval", "bad"]):
            self.assertEqual(client.main(), 1)

    @patch("webbridge.client.send_command")
    def test_bridge_error_returns_one(self, mock_sc):
        mock_sc.side_effect = client.BridgeError("Connection refused")
        with patch("sys.argv", ["webbridge", "ping"]):
            self.assertEqual(client.main(), 1)


class TestMainConfigFlags(unittest.TestCase):
    @patch("webbridge.client.send_command")
    def test_server_flag_passed_through(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "--server", "http://other:1234", "ping"]):
            client.main()
        server_url = mock_sc.call_args[0][0]
        self.assertEqual(server_url, "http://other:1234")

    @patch("webbridge.client.send_command")
    def test_wait_flag_passed_through(self, mock_sc):
        mock_sc.return_value = {"ok": True}
        with patch("sys.argv", ["webbridge", "--wait", "500", "ping"]):
            client.main()
        wait_ms = mock_sc.call_args[1]["wait_ms"]
        self.assertEqual(wait_ms, 500)

    @patch("webbridge.client.send_command")
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


# ===================================================================
# v4 additions: readable, vision, osclick, oshotkey, osscreenshot
# ===================================================================

class TestV4Subcommands(unittest.TestCase):
    def test_readable_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["readable"])
        self.assertEqual(args.command, "readable")
        self.assertEqual(args.max_chars, 20000)

    def test_readable_subcommand_with_options(self):
        p = client.build_parser()
        args = p.parse_args(["readable", "--max-chars", "5000", "--no-a11y", "--console"])
        self.assertEqual(args.command, "readable")
        self.assertEqual(args.max_chars, 5000)
        self.assertTrue(args.no_a11y)
        self.assertTrue(args.console)

    def test_vision_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["vision", "describe this page"])
        self.assertEqual(args.command, "vision")
        self.assertEqual(args.prompt, "describe this page")

    def test_osclick_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["osclick", "100", "200"])
        self.assertEqual(args.command, "osclick")
        self.assertEqual(args.x, 100)
        self.assertEqual(args.y, 200)

    def test_osclick_button_choice(self):
        p = client.build_parser()
        args = p.parse_args(["osclick", "10", "20", "--button", "rightClick"])
        self.assertEqual(args.button, "rightClick")

    def test_ostype_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["ostype", "hello world"])
        self.assertEqual(args.command, "ostype")
        self.assertEqual(args.text, "hello world")

    def test_osscreenshot_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["osscreenshot"])
        self.assertEqual(args.command, "osscreenshot")

    def test_oshotkey_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["oshotkey", "ctrl", "c"])
        self.assertEqual(args.command, "oshotkey")
        self.assertEqual(args.keys, ["ctrl", "c"])

    def test_osmove_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["osmove", "300", "400", "--duration", "0.5"])
        self.assertEqual(args.command, "osmove")
        self.assertEqual(args.x, 300)
        self.assertEqual(args.y, 400)
        self.assertEqual(args.duration, 0.5)

    def test_ospress_subcommand(self):
        p = client.build_parser()
        args = p.parse_args(["ospress", "enter"])
        self.assertEqual(args.command, "ospress")
        self.assertEqual(args.key, "enter")


class TestV4Dispatch(unittest.TestCase):
    @patch("webbridge.client.send_command")
    def test_readable_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": {"textBlock": "x"}}
        with patch("sys.argv", ["webbridge", "readable", "--max-chars", "1000"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        args = mock_sc.call_args
        self.assertEqual(args[0][1], "readable")
        self.assertEqual(args[0][2]["maxChars"], 1000)

    @patch("webbridge.client.send_command")
    def test_vision_dispatches(self, mock_sc):
        mock_sc.return_value = {"ok": True, "value": {"screenshot_path": "/tmp/x.png"}}
        with patch("sys.argv", ["webbridge", "vision", "describe"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        args = mock_sc.call_args
        self.assertEqual(args[0][1], "vision")

    @patch("webbridge.client._http_post")
    def test_osclick_dispatches(self, mock_post):
        mock_post.return_value = {"ok": True, "value": None}
        with patch("sys.argv", ["webbridge", "osclick", "100", "200"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        # _http_post is called with (server, path, body, timeout)
        call = mock_post.call_args
        self.assertEqual(call[0][1], "/os")
        body = call[0][2]
        self.assertEqual(body["action"], "click")
        self.assertEqual(body["args"]["x"], 100)
        self.assertEqual(body["args"]["y"], 200)

    @patch("webbridge.client._http_post")
    def test_oshotkey_dispatches(self, mock_post):
        mock_post.return_value = {"ok": True, "value": None}
        with patch("sys.argv", ["webbridge", "oshotkey", "ctrl", "c"]):
            rc = client.main()
        self.assertEqual(rc, 0)
        body = mock_post.call_args[0][2]
        self.assertEqual(body["action"], "hotkey")
        self.assertEqual(body["args"]["keys"], ["ctrl", "c"])


class TestV4OutputFormatting(unittest.TestCase):
    def test_readable_text_block_printed_in_pretty_mode(self):
        # In pretty mode (no --json), `readable` should print the textBlock
        # as plain text, not JSON — much friendlier for piping to an LLM.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            client._print_result(
                {"ok": True, "value": {"textBlock": "URL: x\nTitle: y\n== VISIBLE TEXT ==\nhello"}},
                raw_json=False,
                command="readable",
            )
        out = buf.getvalue()
        self.assertIn("URL: x", out)
        self.assertIn("== VISIBLE TEXT ==", out)
        self.assertNotIn("{", out)  # not JSON

    def test_vision_suppresses_base64_in_pretty_mode(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            client._print_result(
                {"ok": True, "value": {
                    "screenshot_path": "/tmp/x.png",
                    "png_b64": "iVBORw0KG...(very long base64)...",
                    "readable": {"textBlock": "hello"},
                }},
                raw_json=False,
                command="vision",
            )
        out = buf.getvalue()
        self.assertIn("/tmp/x.png", out)
        self.assertNotIn("iVBORw0KG", out)  # b64 suppressed



if __name__ == "__main__":
    unittest.main()
