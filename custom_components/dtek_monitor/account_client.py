"""Odessa household cabinet API with explicit, non-retried reading submissions."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import hashlib
import json
import math
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from .account import (
    ACCOUNT_PROVIDERS,
    AccountAuthError,
    AccountError,
    AccountRateLimitError,
    AccountSnapshot,
    PersonalAccount,
    normalize_phone,
    parse_consumption,
    parse_readings,
)
from .account_history import (
    MAX_HISTORY_PAGES,
    MAX_HISTORY_ROWS,
    parse_history_page,
    parse_months,
    validate_year,
)

AUTH_ORIGIN = "https://auth.my.dtek-oem.com.ua"
CABINET_ORIGIN = "https://my.dtek-oem.com.ua"
# Public OAuth application identifier shipped by DTEK's own web frontend.
OAUTH_CLIENT_ID = "ee2b502c-e74e-476c-bb1a-dc7e075e2c47"
CALLBACK = CABINET_ORIGIN + "/oauth/callback"
BILLING = "/api/billing-service/api"


def create_account_provider(provider_id: str) -> DTEKAccountClient:
    """Load transport at module import time, outside HA's event loop."""
    if provider_id not in ACCOUNT_PROVIDERS:
        raise AccountError("Personal accounts are not supported for this operator")
    return DTEKAccountClient()


class DTEKAccountClient:
    """Own cookie jar, PKCE login and serialized short-lived API token renewal."""

    def __init__(self, session: Any = None) -> None:
        self._session = session or AsyncSession(
            impersonate="chrome136", timeout=25, allow_redirects=False
        )
        self._lock = asyncio.Lock()
        self._token = ""
        self._token_expires = 0.0
        self._accounts: set[str] = set()
        self.subject: str | None = None

    async def close(self) -> None:
        await self._session.close()
        self._token = ""
        self._accounts.clear()

    async def _request(self, method: str, path: str, *, auth=False, **kwargs):
        origin = AUTH_ORIGIN if auth else CABINET_ORIGIN
        headers = {
            "Accept": "application/json",
            "Origin": origin,
            "Referer": origin + "/",
        }
        if method != "GET":
            csrf = self._session.cookies.get("XSRF-TOKEN")
            if csrf:
                headers["X-CSRF"] = unquote(csrf)
        if self._token and not auth:
            headers["X-Id-Token"] = (
                "Bearer " + self._token if path.startswith(BILLING) else self._token
            )
        try:
            return await self._session.request(
                method, origin + path, headers=headers, **kwargs
            )
        except (RequestException, TimeoutError, OSError):
            # curl errors can contain full URLs including account identifiers.
            raise AccountError(
                "Cannot connect to the personal account service"
            ) from None

    @staticmethod
    def _check(response, *, credentials=False) -> None:
        if response.status_code == 401 or (
            credentials and response.status_code in (400, 422)
        ):
            raise AccountAuthError("Personal account authentication required")
        if response.status_code == 429:
            raise AccountRateLimitError("Personal account request limit reached")
        if response.status_code < 200 or response.status_code >= 300:
            # A protection-system 403 is not evidence of an incorrect password.
            raise AccountError("Personal account service rejected the request")

    @classmethod
    def _decode(cls, response) -> Any:
        cls._check(response)
        try:
            return response.json()
        except (ValueError, TypeError):
            raise AccountError(
                "Personal account returned an invalid response"
            ) from None

    async def authenticate(self, phone: str, password: str) -> str:
        async with self._lock:
            self._token = ""
            self._accounts.clear()
            self._check(await self._request("GET", "/login", auth=True))
            self._check(
                await self._request(
                    "POST",
                    "/api/oauth/api/auth/login",
                    auth=True,
                    json={"login": normalize_phone(phone), "password": password},
                ),
                credentials=True,
            )
            verifier = secrets.token_urlsafe(64)
            state = secrets.token_urlsafe(24)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            response = await self._request(
                "GET",
                "/api/oauth/authorize",
                auth=True,
                params={
                    "redirect_uri": CALLBACK,
                    "client_id": OAUTH_CLIENT_ID,
                    "response_type": "code",
                    "state": state,
                    "code_challenge_method": "S256",
                    "code_challenge": challenge,
                },
            )
            if response.status_code not in (302, 303):
                self._check(response)
                raise AccountAuthError("Personal account login was not completed")
            target = urlsplit(response.headers.get("location", ""))
            query = parse_qs(target.query)
            if (
                target._replace(query="", fragment="").geturl() != CALLBACK
                or query.get("state") != [state]
                or len(query.get("code", [])) != 1
            ):
                raise AccountAuthError("Personal account login redirect was invalid")
            self._check(
                await self._request(
                    "POST",
                    "/api/oauth/token",
                    data={
                        "grant_type": "authorization_code",
                        "client_id": OAUTH_CLIENT_ID,
                        "redirect_uri": CALLBACK,
                        "code": query["code"][0],
                        "code_verifier": verifier,
                    },
                ),
                credentials=True,
            )
            profile = self._decode(await self._request("GET", "/api/oauth/userinfo"))
            if not isinstance(profile, dict) or not isinstance(profile.get("sub"), str):
                raise AccountError("Personal account identity is missing")
            self.subject = profile["sub"]
            await self._issue_token()
            return self.subject

    async def _issue_token(self) -> None:
        response = await self._request("POST", "/api/userData/api/login")
        if response.status_code == 401:
            self._check(
                await self._request(
                    "POST",
                    "/api/oauth/token",
                    data={"grant_type": "refresh_token", "client_id": OAUTH_CLIENT_ID},
                ),
                credentials=True,
            )
            response = await self._request("POST", "/api/userData/api/login")
        data = self._decode(response)
        if not isinstance(data, dict) or not isinstance(data.get("xIdToken"), str):
            raise AccountError("Personal account token is missing")
        token = data["xIdToken"]
        try:
            payload = token.split(".")[1]
            claims = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
            ttl = float(claims["exp"]) - time.time() - 60
            if not math.isfinite(ttl) or ttl <= 0:
                raise ValueError
        except (IndexError, KeyError, ValueError, TypeError):
            raise AccountError("Personal account token is invalid") from None
        self._token = token
        self._token_expires = time.monotonic() + min(ttl, 900)

    async def _ensure_token(self, rejected: str | None = None) -> None:
        async with self._lock:
            if not self.subject:
                raise AccountAuthError("Personal account authentication required")
            if (
                not self._token
                or time.monotonic() >= self._token_expires
                or (rejected is not None and self._token == rejected)
            ):
                await self._issue_token()

    async def _get(self, path: str, **kwargs) -> Any:
        await self._ensure_token()
        token = self._token
        response = await self._request("GET", path, **kwargs)
        if response.status_code == 401:
            await self._ensure_token(rejected=token)
            response = await self._request("GET", path, **kwargs)
        return self._decode(response)

    async def list_accounts(self) -> tuple[PersonalAccount, ...]:
        accounts: dict[str, PersonalAccount] = {}
        for page in range(1, 51):
            response = await self._get(
                "/api/billing/joint/objects/get_advanced",
                params={"page": page, "pageSize": 200},
            )
            if (
                not isinstance(response, dict)
                or response.get("status") != "success"
                or response.get("error")
                or not isinstance(response.get("data"), dict)
                or not isinstance(response["data"].get("data"), list)
            ):
                raise AccountError("Invalid personal account list")
            data = response["data"]
            pages = data.get("totalPages")
            if type(pages) is not int or pages < 0 or pages > 50:
                raise AccountError("Invalid personal account pagination")
            for item in data["data"]:
                if not isinstance(item, dict):
                    raise AccountError("Invalid personal account")
                if item.get("isLegal") is not False:
                    continue
                number, address = item.get("accountNumber"), item.get("address")
                if (
                    not isinstance(number, str)
                    or not number.isascii()
                    or not number.isdigit()
                ):
                    raise AccountError("Invalid personal account identifier")
                if not isinstance(address, str) or not address.strip():
                    raise AccountError("Personal account address is missing")
                accounts[number] = PersonalAccount(number, address)
            if page >= pages:
                self._accounts = set(accounts)
                return tuple(accounts.values())
        raise AccountError("Personal account list is too large")

    async def read_account(self, number: str) -> AccountSnapshot:
        if number not in self._accounts:
            raise AccountError("Selected personal account is no longer linked")
        now = datetime.now(ZoneInfo("Europe/Kyiv"))
        path = f"{BILLING}/meters/household/{number}"
        details = await self._get(path + "/data")
        if not isinstance(details, dict) or details.get("accountNumber") != number:
            raise AccountError("Invalid meter details")
        readings = parse_readings(
            await self._get(path + "/meters-with-readings"), now.date()
        )
        year = now.year if now.month != 1 else now.year - 1
        consumption, period = parse_consumption(
            await self._get(
                BILLING + "/meters/household/customer-book",
                params={"accountNumber": number, "year": year},
            ),
            now.date(),
        )
        verification = details.get("nextVerificationYear")
        if verification in (None, ""):
            verification_year = None
        else:
            try:
                verification_year = int(verification)
                if not 1900 <= verification_year <= 2200:
                    raise ValueError
            except (ValueError, TypeError):
                raise AccountError("Invalid meter verification year") from None
        model = details.get("meterType")
        if model is not None and not isinstance(model, str):
            raise AccountError("Invalid meter model")
        return AccountSnapshot(
            model, verification_year, readings, consumption, period, now
        )

    async def read_history(self, number: str, year: int) -> dict:
        now = datetime.now(ZoneInfo("Europe/Kyiv"))
        validate_year(year, now.date())
        if number not in self._accounts:
            raise AccountError("Selected personal account is no longer linked")
        records = {}
        expected_pages = None
        raw_count = 0
        for page in range(1, MAX_HISTORY_PAGES + 1):
            payload = await self._get(
                f"{BILLING}/meters/household/{number}/history",
                params={"year": year, "page": page, "limit": 100},
            )
            returned_page, pages, rows = parse_history_page(payload, year, now.date())
            if returned_page != page or (
                expected_pages is not None and pages != expected_pages
            ):
                raise AccountError("History changed during pagination; retry later")
            expected_pages = pages
            raw_count += len(rows)
            if raw_count > MAX_HISTORY_ROWS or (pages > 1 and not rows):
                raise AccountError("Incomplete or excessive history response")
            for identity, row in rows:
                if identity in records and records[identity] != row:
                    raise AccountError("Conflicting history records; retry later")
                records[identity] = row
            if page >= pages:
                break
        months = parse_months(
            await self._get(
                BILLING + "/meters/household/customer-book",
                params={"accountNumber": number, "year": year},
            ),
            year,
            now.date(),
        )
        return {
            "year": year,
            "fetched_at": datetime.now(ZoneInfo("Europe/Kyiv")).isoformat(),
            "time_zone": "Europe/Kyiv",
            "meter_scope": "account",
            "readings": sorted(
                records.values(),
                key=lambda row: (row["date_time"], row["zone"], row["energy"]),
                reverse=True,
            ),
            "months": months,
        }

    async def read_submission_context(self, number: str) -> dict:
        from .account_submission import submission_context

        # Recheck household membership for every prepare/confirm, not just on the
        # six-hour sensor refresh. A removed account must not remain writable.
        await self.list_accounts()
        if number not in self._accounts:
            raise AccountError("Selected personal account is no longer linked")
        path = f"{BILLING}/meters/household/{number}"
        details = await self._get(path + "/data")
        readings = await self._get(path + "/meters-with-readings")
        return submission_context(
            details, readings, number, datetime.now(ZoneInfo("Europe/Kyiv")).date()
        )

    async def submit_readings(self, number: str, readings: list, authorize) -> dict:
        from .account_submission import SubmissionError, submission_result

        await self._ensure_token()
        authorize()
        if number not in self._accounts:
            raise SubmissionError("not_available")
        body = {
            "accountNumber": number,
            "readings": [
                {
                    "deviceEnergyType": "ACTIVE",
                    "deviceScaleType": r["zone"],
                    "value": r["value"],
                }
                for r in readings
            ],
        }
        # Unlike GET, never retry this POST, including a 401 or a lost response.
        try:
            response = await self._request(
                "POST", f"{BILLING}/meters/household/{number}/readings", json=body
            )
            payload = self._decode(response)
        except AccountError:
            return {"status": "unknown"}
        return submission_result(payload, len(readings))
