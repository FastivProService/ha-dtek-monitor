"""Transport regressions adapted from Zerstoren/ha-dtek-krem-monitor (MIT)."""

from __future__ import annotations

import asyncio
import re
import types
import unittest
from unittest import mock

from component_loader import load_component_module

dtek_client = load_component_module("dtek_client")

STUB = (
    '<html style="height:100%"><head>'
    '<META NAME="ROBOTS" CONTENT="NOINDEX, NOFOLLOW"></head><body>'
    '<iframe id="main-iframe" src="/_Incapsula_Resource?SWUDNSAI=31">'
    "Request unsuccessful. Incapsula incident ID: 1</iframe></body></html>"
)
REAL = (
    '<html><head><meta name="csrf-token" content="tok-1"></head><body>'
    + "x" * 3000
    + '<script src="/_Incapsula_Resource?SWJIYLWA=abc&ns=4"></script>'
    + "</body></html>"
)


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def json(self):
        import json

        return json.loads(self.text)


class _Jar(list):
    def set_cookie(self, cookie) -> None:
        self.append(cookie)


class _FakeSession:
    """Minimal stand-in for curl_cffi AsyncSession recording requests."""

    def __init__(self, gets: list, posts: list | None = None) -> None:
        self._gets = list(gets)
        self._posts = list(posts or [])
        self.calls: list[tuple[str, dict]] = []
        self.cookies = types.SimpleNamespace(jar=_Jar())
        self.connections = 1

    async def get(self, url, headers=None, **_kw):
        self.calls.append(("GET", dict(headers or {})))
        item = self._gets.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, _Resp) else _Resp(item)

    async def post(self, url, data=None, headers=None, **_kw):
        self.calls.append(("POST", dict(headers or {})))
        item = self._posts.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, _Resp) else _Resp(item)

    async def close(self):
        return None


def _client_with(session: _FakeSession, test: unittest.TestCase):
    """Client whose 'new connections' keep talking to the same fake server."""
    client = dtek_client.DTEKClient()
    client._http = session

    def _new_connection(target=""):
        session.connections += 1
        session.targets = getattr(session, "targets", []) + [target]
        return session

    patcher = mock.patch.object(dtek_client, "_create_session", _new_connection)
    patcher.start()
    test.addCleanup(patcher.stop)
    return client


class HeaderProfileTests(unittest.TestCase):
    def test_no_hand_written_user_agent(self) -> None:
        """UA/client hints must come from curl_cffi so they match TLS."""
        for headers in (dtek_client.PAGE_HEADERS, dtek_client.XHR_HEADERS):
            lowered = {k.lower() for k in headers}
            self.assertNotIn("user-agent", lowered)
            self.assertNotIn("sec-ch-ua", lowered)

    def test_xhr_headers_describe_cors_fetch_not_navigation(self) -> None:
        xhr = dtek_client.XHR_HEADERS
        self.assertEqual(xhr["Sec-Fetch-Mode"], "cors")
        self.assertEqual(xhr["Sec-Fetch-Dest"], "empty")
        self.assertEqual(xhr["Sec-Fetch-Site"], "same-origin")
        self.assertIsNone(xhr["Sec-Fetch-User"])
        self.assertIsNone(xhr["Upgrade-Insecure-Requests"])
        self.assertIn("application/json", xhr["Accept"])

    def test_impersonate_target_is_plain_desktop_chrome(self) -> None:
        self.assertRegex(dtek_client.IMPERSONATE_TARGET, r"^chrome\d+$")
        self.assertGreaterEqual(
            int(re.sub(r"\D", "", dtek_client.IMPERSONATE_TARGET)), 120
        )

    def test_candidate_targets_skip_unknown_and_bleeding_edge(self) -> None:
        supported = {"chrome120", "chrome131", "chrome150"}
        self.assertEqual(
            dtek_client._candidate_targets(supported), ["chrome131", "chrome120"]
        )
        self.assertEqual(dtek_client._candidate_targets(set()), ["chrome120"])

    def test_curl_http2_error_is_transport_error(self) -> None:
        err = OSError("Failed to perform, curl: (16) . See https://curl.se/...")
        self.assertTrue(dtek_client._is_transport_error(err))
        self.assertFalse(dtek_client._is_transport_error(OSError("DNS failure")))


class ProtectionDetectionTests(unittest.TestCase):
    def test_real_page_with_embedded_incapsula_script_is_not_protection(self) -> None:
        """Live regression: the 900 KB page embeds _Incapsula_Resource."""
        html = (
            '<!DOCTYPE html><html lang="uk"><head>'
            '<meta name="csrf-token" content="tok-live"></head><body>'
            "<script>DisconSchedule.streets = {};</script>"
            '<script src="/_Incapsula_Resource?SWJIYLWA=719d&ns=4&cb=1"></script>'
            "</body></html>"
        )
        self.assertFalse(dtek_client._is_protection_page(html))

    def test_stub_without_token_is_protection(self) -> None:
        self.assertTrue(dtek_client._is_protection_page(STUB))


class HandshakeTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(dtek_client.asyncio, "sleep", mock.AsyncMock())
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_page_retry_waits_and_marks_same_origin_reload(self) -> None:
        session = _FakeSession([STUB, REAL])
        client = _client_with(session, self)
        html = asyncio.run(client._load_shutdowns_page())
        self.assertEqual(html, REAL)
        self.assertEqual(self.sleep.await_count, 1)  # no wait before attempt 1
        first, second = session.calls[0][1], session.calls[1][1]
        self.assertNotIn("Sec-Fetch-Site", first)
        self.assertEqual(second["Sec-Fetch-Site"], "same-origin")
        self.assertEqual(second["Referer"], dtek_client.DTEK_SHUTDOWNS_URL)

    def test_stub_served_with_403_is_retried(self) -> None:
        """Live regression: Incapsula now answers the first GET with 403."""
        session = _FakeSession([_Resp(STUB, 403), REAL])
        client = _client_with(session, self)
        self.assertEqual(asyncio.run(client._load_shutdowns_page()), REAL)
        self.assertEqual(client.waf_challenges, 1)

    def test_stub_then_new_connection_with_cookies(self) -> None:
        """Live behaviour on HA OS: after a 403 stub reuse fails, a new
        connection carrying the Incapsula cookies gets the real page."""
        session = _FakeSession([_Resp(STUB, 403), REAL])
        session.cookies.jar.append("visid_incap_1")
        client = _client_with(session, self)
        self.assertEqual(asyncio.run(client._load_shutdowns_page()), REAL)
        self.assertEqual(session.connections, 2)
        self.assertIn("visid_incap_1", session.cookies.jar)

    def test_curl16_retries_on_new_connection_same_profile(self) -> None:
        session = _FakeSession([OSError("Failed to perform, curl: (16) ."), REAL])
        client = _client_with(session, self)
        start_target = client.impersonate_target
        self.assertEqual(asyncio.run(client._load_shutdowns_page()), REAL)
        self.assertEqual(client.impersonate_target, start_target)
        self.assertEqual(client.transport_errors, 1)

    def test_two_curl16_in_a_row_switch_profile(self) -> None:
        err = OSError("Failed to perform, curl: (16) .")
        session = _FakeSession([err, err, REAL])
        client = _client_with(session, self)
        client._targets = ["chrome136", "chrome131"]
        self.assertEqual(asyncio.run(client._load_shutdowns_page()), REAL)
        self.assertEqual(client.impersonate_target, "chrome131")
        self.assertEqual(client.transport_errors, 2)

    def test_transport_error_on_last_attempt_raises(self) -> None:
        err = OSError("Failed to perform, curl: (16) .")
        session = _FakeSession([err, err, err])
        client = _client_with(session, self)
        client._targets = ["chrome120"]
        with self.assertRaises(OSError):
            asyncio.run(client._load_shutdowns_page())
        self.assertEqual(client.transport_errors, 3)

    def test_ajax_curl16_retries_on_new_connection(self) -> None:
        session = _FakeSession(
            gets=[REAL],
            posts=[OSError("Failed to perform, curl: (16) ."), '{"result": true}'],
        )
        client = _client_with(session, self)
        self.assertTrue(asyncio.run(client._post({"method": "x"}))["result"])
        self.assertEqual(client.transport_errors, 1)

    def test_plain_403_without_waf_page_is_an_error(self) -> None:
        session = _FakeSession([_Resp("<html>Forbidden</html>", 403)])
        client = _client_with(session, self)
        with self.assertRaises(dtek_client.DTEKApiError):
            asyncio.run(client._load_shutdowns_page())

    def test_ajax_403_waf_page_triggers_rehandshake(self) -> None:
        session = _FakeSession(
            gets=[REAL, REAL],
            posts=[_Resp(STUB, 403), '{"result": true}'],
        )
        client = _client_with(session, self)
        self.assertTrue(asyncio.run(client._post({"method": "x"}))["result"])

    def test_hard_block_drops_cookies_and_raises_protection_error(self) -> None:
        session = _FakeSession([STUB, STUB, STUB])
        client = _client_with(session, self)
        with self.assertRaises(dtek_client.DTEKProtectionError):
            asyncio.run(client._refresh_session())
        self.assertIsNone(client._http)
        self.assertEqual(client.waf_hard_blocks, 1)

    def test_ajax_waf_page_triggers_rehandshake_then_succeeds(self) -> None:
        session = _FakeSession(
            gets=[REAL, REAL],
            posts=[STUB, '{"result": true, "data": {}}'],
        )
        client = _client_with(session, self)
        result = asyncio.run(client._post({"method": "getHomeNum"}))
        self.assertTrue(result["result"])
        methods = [m for m, _ in session.calls]
        self.assertEqual(methods, ["GET", "POST", "GET", "POST"])
        post_headers = session.calls[1][1]
        self.assertEqual(post_headers["X-Csrf-Token"], "tok-1")
        self.assertEqual(post_headers["Sec-Fetch-Mode"], "cors")
