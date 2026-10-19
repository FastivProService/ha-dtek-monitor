"""Transient SMS registration/recovery, separate from cabinet and billing state."""

from __future__ import annotations

import math
import re
import time
from functools import wraps

from .account import AccountError, normalize_phone
from .account_client import DTEKAccountClient
from .account_providers import AccountProviderSpec

SMS_INTERVAL = 60
MAX_CODE_ATTEMPTS = 5


def _serialized(method):
    """A second form submission must observe the first one's completed state."""

    @wraps(method)
    async def run(self, *args, **kwargs):
        async with self._lock:
            return await method(self, *args, **kwargs)

    return run


class AccessError(AccountError):
    """A public translation key, never an upstream response or credential."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_password(password: str, confirmation: str) -> None:
    """Match the current DTEK form's password rules before making a request."""
    if (
        not isinstance(password, str)
        or not 8 <= len(password) <= 64
        or re.search(r"\s", password)
        or len(re.findall(r"[A-ZА-ЯЄІЇҐ]", password)) < 2
        or not re.search(r"[a-zа-яєіїґ]", password)
        or not re.search(r"[0-9]", password)
    ):
        raise AccessError("access_password_rules")
    if password != confirmation:
        raise AccessError("access_password_mismatch")


class AccountAccessClient(DTEKAccountClient):
    """One short-lived challenge; no automatic SMS or finalization retries."""

    def __init__(self, session=None, *, spec: AccountProviderSpec | None = None):
        super().__init__(session, spec=spec)
        self.kind = ""
        self.phone = ""
        self.phase = "new"
        self._challenge = ""
        self._retry_at = 0.0
        self._attempts = 0

    @property
    def retry_seconds(self) -> int:
        return max(0, math.ceil(self._retry_at - time.monotonic()))

    def _limit(self, response) -> None:
        headers = {k.lower(): v for k, v in response.headers.items()}
        try:
            seconds = float(headers.get("retry-after", SMS_INTERVAL))
            if not math.isfinite(seconds):
                raise ValueError
        except (TypeError, ValueError):
            seconds = SMS_INTERVAL
        self._retry_at = max(
            self._retry_at, time.monotonic() + max(SMS_INTERVAL, seconds)
        )

    async def _send(self, action: str, payload: dict, method="POST"):
        try:
            response = await self._request(
                method,
                f"/api/oauth/api/auth/{self.kind}/{action}",
                auth=True,
                json=payload,
            )
        except AccountError:
            raise AccessError("access_cannot_connect") from None
        status = response.status_code
        if status == 429:
            self._limit(response)
            raise AccessError("access_rate_limit")
        if status == 404:
            raise AccessError(
                "access_not_registered" if action == "start" else "access_expired"
            )
        if status == 409 and action == "start":
            raise AccessError("access_already_registered")
        if status in (400, 422):
            raise AccessError(
                "access_invalid_code" if action == "verify" else "access_invalid_data"
            )
        if not 200 <= status < 300:
            raise AccessError("access_cannot_connect")
        return response

    @_serialized
    async def start(self, kind: str, phone: str) -> None:
        if kind not in ("registration", "password-recovery") or self.phase not in (
            "new",
            "code",
        ):
            raise AccessError("access_expired")
        phone = normalize_phone(phone)
        if self.retry_seconds:
            raise AccessError("access_wait")
        # Even an ambiguous network failure must not trigger an immediate resend.
        self._retry_at = time.monotonic() + SMS_INTERVAL
        self.kind, self.phone = kind, phone
        self._challenge = ""
        self.phase = "new"
        self._check(
            await self._request(
                "GET", "/signup" if kind == "registration" else "/recovery", auth=True
            )
        )
        try:
            response = await self._send("start", {"login": phone})
        except AccessError as err:
            if err.code == "access_cannot_connect":
                raise AccessError("access_start_uncertain") from None
            raise
        self._limit(response)
        key = "registration_id" if kind == "registration" else "recovery_id"
        try:
            value = response.json()[key]
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise AccessError("access_start_uncertain") from None
        self._challenge = value
        self._attempts = 0
        self.phase = "code"

    def _payload(self, phase: str) -> dict:
        if self.phase != phase or not self._challenge:
            raise AccessError("access_expired")
        key = "registration_id" if self.kind == "registration" else "recovery_id"
        return {key: self._challenge}

    @_serialized
    async def verify(self, code: str) -> None:
        payload = self._payload("code")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{4}", code):
            raise AccessError("access_code_format")
        if self._attempts >= MAX_CODE_ATTEMPTS:
            raise AccessError("access_attempts_exhausted")
        self._attempts += 1
        await self._send("verify", {**payload, "verification_code": code})
        self.phase = "profile" if self.kind == "registration" else "password"

    @_serialized
    async def profile(
        self, first_name: str, last_name: str, middle_name: str = ""
    ) -> None:
        payload = self._payload("profile")
        values = {
            "first_name": first_name,
            "last_name": last_name,
            "middle_name": middle_name,
        }
        if (
            any(not isinstance(v, str) or len(v.strip()) > 100 for v in values.values())
            or not first_name.strip()
            or not last_name.strip()
        ):
            raise AccessError("access_invalid_data")
        await self._send(
            "profile", {**payload, **{k: v.strip() for k, v in values.items()}}, "PATCH"
        )
        self.phase = "password"

    @_serialized
    async def finalize(
        self, password: str, confirmation: str, *, consent=False
    ) -> None:
        payload = self._payload("password")
        validate_password(password, confirmation)
        if self.kind == "registration":
            if consent is not True:
                raise AccessError("access_consent_required")
            payload["accept_terms_of_service"] = True
        payload.update(password=password, confirm_password=confirmation)
        # A lost response may follow a successful password change. Never retry it.
        self.phase = "uncertain"
        try:
            await self._send("finalize", payload)
        except AccessError as err:
            if err.code in ("access_invalid_data", "access_rate_limit"):
                self.phase = "password"
                raise
            if err.code == "access_expired":
                raise
            self._challenge = ""
            raise AccessError("access_finalize_uncertain") from None
        self.phase = "complete"
        self._challenge = ""

    @_serialized
    async def close(self) -> None:
        self._challenge = self.phone = ""
        self.phase = "closed"
        await super().close()
