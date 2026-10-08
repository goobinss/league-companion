"""LCU discovery and transport tests; never contact a running client."""

import base64
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from league_companion.lcu import (
    Credentials, LcuClient, LcuError, _NoRedirect, find_credentials, read_lockfile,
)


class DiscoveryTests(unittest.TestCase):
    def test_credentials_repr_hides_password(self):
        credentials = Credentials(54321, "private-session-token", 1234)
        self.assertNotIn(credentials.password, repr(credentials))
        self.assertNotIn(credentials.password, str(credentials))

    def test_valid_lockfile_reads_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lockfile"
            path.write_text("LeagueClient:1234:54321:private-session-token:https\n", encoding="utf-8")
            self.assertEqual(read_lockfile(path), Credentials(54321, "private-session-token", 1234))

    def test_malformed_lockfiles_are_rejected(self):
        contents = [
            "", "LeagueClient:1234:54321:token", "LeagueClient:1234:54321:token:https:extra",
            "LeagueClient:1234:54321:token:http", "LeagueClient:1234:54321::https",
            "LeagueClient:0:54321:token:https", "LeagueClient:-1:54321:token:https",
            "LeagueClient:pid:54321:token:https", "LeagueClient:1234:port:token:https",
            "LeagueClient:1234:0:token:https", "LeagueClient:1234:65536:token:https",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lockfile"
            for content in contents:
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(ValueError):
                    read_lockfile(path)

    def test_explicit_missing_path_does_not_fall_back(self):
        with patch("league_companion.lcu.read_lockfile", side_effect=FileNotFoundError) as read:
            with patch("league_companion.lcu._process_lockfiles") as discover:
                self.assertIsNone(find_credentials("/manual/lockfile"))
        read.assert_called_once_with(Path("/manual/lockfile"))
        discover.assert_not_called()

    def test_explicit_invalid_path_does_not_fall_back(self):
        with patch("league_companion.lcu.read_lockfile", side_effect=ValueError) as read:
            with patch("league_companion.lcu._process_lockfiles") as discover:
                self.assertIsNone(find_credentials("/manual/lockfile"))
        read.assert_called_once_with(Path("/manual/lockfile"))
        discover.assert_not_called()

    def test_automatic_discovery_uses_process_path_after_defaults_fail(self):
        credentials = Credentials(54321, "token", 1234)
        process_path = Path("/nonstandard/lockfile")

        def read(path):
            if path == process_path:
                return credentials
            raise FileNotFoundError

        with patch("league_companion.lcu.read_lockfile", side_effect=read):
            with patch("league_companion.lcu._process_lockfiles", return_value=[process_path]) as discover:
                self.assertEqual(find_credentials(), credentials)
        discover.assert_called_once_with()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.credentials = Credentials(54321, "private-session-token", 1234)
        self.client = LcuClient(self.credentials)
        self.opener = Mock()
        self.client._opener = self.opener

    def respond(self, body):
        self.opener.open.return_value = io.BytesIO(body)

    def test_requests_use_literal_loopback_auth_json_and_bounded_timeout(self):
        self.respond(b'{"ok":true}')
        result = self.client.request("PATCH", "/lol-champ-select/v1/session/my-selection", {"spell1Id": 4})
        self.assertEqual(result, {"ok": True})
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://127.0.0.1:54321/lol-champ-select/v1/session/my-selection")
        self.assertEqual(request.get_method(), "PATCH")
        expected = "Basic " + base64.b64encode(b"riot:private-session-token").decode()
        self.assertEqual(request.get_header("Authorization"), expected)
        self.assertEqual(json.loads(request.data), {"spell1Id": 4})
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 2})

    def test_unsafe_paths_are_rejected_before_transport(self):
        paths = [
            "https://example.com/", "http://127.0.0.1/", "//example.com/", "endpoint", "",
            "/path?redirect=elsewhere", "/path#fragment", "/path\\evil",
            "/path\r\nAuthorization: injected",
        ]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.client.request("GET", path)
        self.opener.open.assert_not_called()

    def test_unsupported_methods_and_nan_bodies_never_reach_transport(self):
        for method in ("CONNECT", "TRACE", "get", "POST\r\n"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.client.request(method, "/test")
        with self.assertRaises(ValueError):
            self.client.request("POST", "/test", {"value": float("nan")})
        self.opener.open.assert_not_called()

    def test_proxy_environment_is_ignored_and_redirect_handler_is_installed(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "https://proxy.example", "HTTP_PROXY": "http://proxy.example"}):
            with patch("league_companion.lcu.urllib.request.build_opener") as build:
                LcuClient(self.credentials)
        handlers = build.call_args.args
        proxy = next(handler for handler in handlers if isinstance(handler, urllib.request.ProxyHandler))
        self.assertEqual(proxy.proxies, {})
        self.assertTrue(any(isinstance(handler, _NoRedirect) for handler in handlers))
        https = next(handler for handler in handlers if isinstance(handler, urllib.request.HTTPSHandler))
        self.assertEqual(https._context.verify_mode, ssl.CERT_NONE)
        self.assertFalse(https._context.check_hostname)

    def test_cross_host_redirect_is_not_followed(self):
        request = urllib.request.Request("https://127.0.0.1:54321/test")
        handler = _NoRedirect()
        parent = Mock()
        handler.add_parent(parent)
        self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, "https://example.com/"))
        self.assertIsNone(handler.http_error_302(
            request, io.BytesIO(), 302, "Found", {"location": "https://example.com/"}))
        parent.open.assert_not_called()

    def test_http_errors_expose_status_but_never_server_body_or_credentials(self):
        secret = self.credentials.password
        encoded_secret = base64.b64encode(f"riot:{secret}".encode()).decode()
        self.opener.open.side_effect = urllib.error.HTTPError(
            f"https://riot:{secret}@127.0.0.1:54321/test", 403,
            f"server echoed {secret} {encoded_secret}", {}, io.BytesIO(secret.encode()),
        )
        with self.assertRaises(LcuError) as raised:
            self.client.request("POST", "/test")
        self.assertEqual(raised.exception.status, 403)
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(encoded_secret, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_connection_errors_are_sanitized(self):
        for error in (urllib.error.URLError(self.credentials.password), TimeoutError(self.credentials.password), OSError(self.credentials.password)):
            self.opener.open.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(LcuError) as raised:
                self.client.request("GET", "/test")
            self.assertNotIn(self.credentials.password, str(raised.exception))
            self.assertTrue(raised.exception.__suppress_context__)

    def test_empty_response_and_integer_request_body(self):
        self.respond(b"")
        self.assertIsNone(self.client.request("PUT", "/lol-perks/v1/currentpage", 1234))
        self.assertEqual(self.opener.open.call_args.args[0].data, b"1234")

    def test_invalid_json_and_oversized_responses_are_rejected(self):
        for body in (b"not json", b"\xff", b"x" * (4 * 1024 * 1024 + 1)):
            self.respond(body)
            with self.subTest(size=len(body)), self.assertRaises(LcuError):
                self.client.request("GET", "/test")


if __name__ == "__main__":
    unittest.main()
