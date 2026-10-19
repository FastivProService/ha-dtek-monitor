"""Optional account-scoped outage groups via DTEK's first-party legacy handoff.

Only read routes are allowed. The old cabinet owns a separate session/token; its
failure never invalidates modern meter data or public building schedules.
"""

import asyncio
import base64
from dataclasses import dataclass, field
import json
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from .account import AccountAPIChanged, AccountAuthError, AccountError, AccountRateLimitError
from .account_providers import AccountProviderSpec

INTERFACE = "account_groups"
# Public OAuth challenge from the official old cabinet /auth frontend. Its
# server supplies the matching verifier when exchanging the authorization code.
LEGACY_CHALLENGE = "T0wiJiK1mBexC-LFD46zLufVr-iPXXPSYDMxrsYp_U8"
ROUTES = ("/api/auth/sms", "/api/person/objects/info", "/api/post-powertrack-api")


def _changed():
    return AccountAPIChanged(INTERFACE, "response_format")


def parse_groups(payload):
    """Accept explicit empty, comma-separated or array groups, never partial data."""
    if not isinstance(payload, dict) or "respType" not in payload:
        raise _changed()
    if payload["respType"] != "SUCCESS":
        raise AccountError("Account groups are unavailable")
    if "gpv" not in payload:
        raise _changed()
    value = payload["gpv"]
    if value is None or value == "" or value == []:
        return ()
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list) or len(items) > 12:
        raise _changed()
    groups = set()
    for item in items:
        if not isinstance(item, str):
            raise _changed()
        group = item.strip().upper().replace("ГПВ", "GPV")
        if not re.fullmatch(r"GPV[1-6]\.[12]", group):
            raise _changed()
        groups.add(group)
    return tuple(sorted(groups))


@dataclass(frozen=True)
class GroupAuthorization:
    code: str = field(repr=False)
    user_data_id: str | int = field(repr=False)


class AccountGroupAuthorizationMixin:
    """Use the modern authenticated session only to request a separate code."""

    def account_group_eics(self, number):
        """Cross-check old metadata against the modern linked-object identity."""
        values = self._linked_profiles.get(number, {}).get("eic", [])
        if not isinstance(values, list):
            return ()
        return tuple(v.strip().upper() for v in values if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9]{16}", v.strip()))

    async def authorize_account_groups(self, number):
        if not self.spec.group_origin or not self.spec.group_site:
            raise AccountError("Account groups are not supported for this operator")
        await self._ensure_token()
        async with self._lock:
            if number not in self._accounts:
                raise AccountError("Account is not linked")
            try:
                encoded = self._token.split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
                user_id = claims["user_data_id"]
                if type(user_id) not in (str, int) or not str(user_id):
                    raise ValueError
            except (KeyError, IndexError, ValueError, TypeError):
                raise _changed() from None
            callback = self.spec.group_origin + "/auth/callback"
            state = json.dumps({
                "account": number, "userDataId": user_id, "userTypeId": "2",
                "forcedPersonType": False, "pathDestination": "/cabinet/person/sub-book",
                "nonce": secrets.token_urlsafe(32),
            }, separators=(",", ":"))
            # Do not route optional failures through the main login's repair hook.
            try:
                response = await self._session.request(
                    "GET", self.spec.auth_origin + "/api/oauth/authorize",
                    params={
                        "client_id": self.spec.oauth_client_id.upper(),
                        "response_type": "code", "redirect_uri": callback, "state": state,
                        "code_challenge": LEGACY_CHALLENGE, "code_challenge_method": "S256",
                    },
                    headers={"Accept": "application/json"}, allow_redirects=False,
                )
            except (RequestException, TimeoutError, OSError):
                raise AccountError("Account group authorization is unavailable") from None
            _check_status(response.status_code, redirect=True)
            location = response.headers.get("location", "")
            try:
                if not isinstance(location, str):
                    raise ValueError
                target = urlsplit(location)
            except ValueError:
                raise AccountAuthError("Account group authorization redirect is invalid") from None
            query = parse_qs(target.query)
            if (target._replace(query="", fragment="").geturl() != callback
                or target.fragment or query.get("state") != [state]
                or len(query.get("code", [])) != 1):
                raise AccountAuthError("Account group authorization was not completed")
            return GroupAuthorization(query["code"][0], user_id)


def _check_status(status, *, redirect=False):
    if status in (404, 410):
        raise AccountAPIChanged(INTERFACE, f"HTTP {status}")
    if status == 401:
        raise AccountAuthError("Account group session expired")
    if status == 429:
        raise AccountRateLimitError("Account group request limit reached")
    if status not in ((302, 303) if redirect else (200,)):
        raise AccountError("Account group service is unavailable")


class AccountGroupClient:
    """Independent legacy transport; one bounded renewal per selected account."""

    def __init__(self, modern, session=None):
        self.modern, self.spec = modern, modern.spec
        if not self.spec.group_origin or not self.spec.group_site:
            raise AccountError("Account groups are not supported for this operator")
        self._session = session or AsyncSession(
            impersonate="chrome136", timeout=15, allow_redirects=False
        )
        self._tokens = {}
        self._lock = asyncio.Lock()

    async def close(self):
        self._tokens.clear()
        await self._session.close()

    async def _post(self, path, data, authorization):
        if path not in ROUTES:
            raise ValueError("Unsupported account group route")
        try:
            response = await self._session.request(
                "POST", self.spec.group_origin + path,
                json=data, headers={
                    "Accept": "application/json", "Origin": self.spec.group_origin,
                    "Referer": self.spec.group_origin + "/", "Authorization": authorization,
                }, allow_redirects=False,
            )
        except (RequestException, TimeoutError, OSError):
            raise AccountError("Account group service is unavailable") from None
        _check_status(response.status_code)
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise _changed() from None
        if not isinstance(payload, dict):
            raise _changed()
        if payload.get("status") in ("fail", "error", "FAIL", "ERROR") or payload.get("error"):
            raise AccountError("Account group request was rejected")
        return payload

    async def _login(self, number):
        grant = await self.modern.authorize_account_groups(number)
        basic = base64.b64encode(f"{number}:{grant.code}".encode()).decode()
        payload = await self._post("/api/auth/sms", {
            "action": "token", "userType": "person", "userTypeId": "2",
            "userDataId": grant.user_data_id, "forcedPersonType": False,
            "account": number, "redirectUri": self.spec.group_origin + "/auth/callback",
            "site": self.spec.group_site, "language": "uk-UA", "platform": "Linux",
        }, "Basic " + basic)
        user = payload.get("user")
        token = user.get("token") if isinstance(user, dict) else None
        if payload.get("status") != "success" or not isinstance(token, str) or not token:
            raise _changed()
        self._tokens[number] = token

    async def read_groups(self, number):
        async with self._lock:
            for attempt in range(2):
                try:
                    if number not in self._tokens:
                        await self._login(number)
                    token = self._tokens[number]
                    payload = await self._post("/api/person/objects/info", {
                        "token": token, "account": number,
                    }, "Bearer " + token)
                    customer = payload.get("customer")
                    if not isinstance(customer, dict) or str(customer.get("account")) != number:
                        raise _changed()
                    eic = customer.get("eic")
                    if not isinstance(eic, str) or not re.fullmatch(r"[A-Za-z0-9]{16}", eic):
                        raise _changed()
                    known_eics = self.modern.account_group_eics(number)
                    if known_eics and eic.upper() not in known_eics:
                        raise AccountError("Account group EIC does not match the linked account")
                    payload = await self._post("/api/post-powertrack-api", {
                        "token": token, "account": number, "eic": eic, "site": self.spec.group_site,
                    }, "Bearer " + token)
                    return parse_groups(payload)
                except AccountAuthError:
                    self._tokens.pop(number, None)
                    if attempt:
                        raise
                except AccountError:
                    # Some old routes reject an expired session in a 200/fail
                    # body. Do not retry ambiguous failures immediately, but let
                    # the next scheduled check obtain a fresh legacy token.
                    self._tokens.pop(number, None)
                    raise


def create_account_group_provider(modern):
    spec = getattr(modern, "spec", None)
    if (isinstance(spec, AccountProviderSpec) and spec.group_origin and spec.group_site
        and callable(getattr(modern, "authorize_account_groups", None))):
        return AccountGroupClient(modern)
    return None
