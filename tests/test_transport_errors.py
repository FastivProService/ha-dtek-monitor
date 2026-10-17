"""Bounded recovery and malformed-response regressions."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from test_browser_profile import REAL, STUB, _FakeSession, _Resp, _client_with
from component_loader import load_component_module

m = load_component_module("dtek_client")


class TransportErrorsTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(m.asyncio, "sleep", AsyncMock())
        p.start()
        self.addCleanup(p.stop)

    def test_csrf_variants(self):
        for html in [
            '<meta name="csrf-token" content="test-token">',
            "<meta content='test-token' name='csrf_token'>",
            '<input name="_token" value="test-token">',
            '<input value="test-token" name="_token">',
            'window.Laravel = { csrfToken: "test-token" };',
        ]:
            with self.subTest(html=html):
                self.assertEqual(m._extract_csrf_token(html), "test-token")

    def test_csrf_failure_retries_only_once(self):
        session = _FakeSession([REAL, REAL], [_Resp("", 400), _Resp("", 400)])
        client = _client_with(session, self)
        with self.assertRaises(m.DTEKAuthError):
            asyncio.run(client._post({"method": "x"}))
        self.assertEqual(len(session.calls), 4)

    def test_protection_failure_retries_only_once(self):
        session = _FakeSession([REAL, REAL], [STUB, STUB])
        client = _client_with(session, self)
        with self.assertRaises(m.DTEKProtectionError):
            asyncio.run(client._post({"method": "x"}))
        self.assertEqual(len(session.calls), 4)

    def test_invalid_json_and_response_shapes_fail(self):
        for payload in ("<html>down</html>", "[]", "{}", "null"):
            with self.subTest(payload=payload):
                client = _client_with(_FakeSession([REAL], [payload]), self)
                with self.assertRaises(m.DTEKApiError):
                    asyncio.run(client.check_update("17.10.2026 12:00"))

    def test_schedule_no_change_must_be_explicit(self):
        for payload in ('{"result":null}', '{"result":true}'):
            client = _client_with(_FakeSession([REAL], [payload]), self)
            with self.assertRaises(m.DTEKApiError):
                asyncio.run(client.check_update("17.10.2026 12:00"))
        client = _client_with(_FakeSession([REAL], ['{"result":false}']), self)
        self.assertIsNone(asyncio.run(client.check_update("17.10.2026 12:00")))

    def test_timeouts_are_wrapped_and_bounded(self):
        client = _client_with(_FakeSession([TimeoutError()] * 3), self)
        with self.assertRaises(m.DTEKApiError):
            asyncio.run(client._refresh_session())
        self.assertEqual(client.transport_errors, 3)

    def test_close_invalidates_csrf(self):
        client = _client_with(_FakeSession([REAL]), self)

        async def run():
            await client._ensure_session()
            await client.close()

        asyncio.run(run())
        self.assertIsNone(client._http)
        self.assertIsNone(client._csrf_token)

    def test_diagnostic_does_not_log_html_tokens(self):
        self.assertNotIn("tok-1", m._html_debug_summary(REAL))
