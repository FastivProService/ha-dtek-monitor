"""Explicit household cabinet links, independent of HA's monitored selection."""

from __future__ import annotations

import re

from .account import AccountError, AccountAPIChanged, PersonalAccount

LINKS = "/api/billing/joint/objects"


class AccountLinkError(AccountError):
    """A safe translation key; never include upstream account data in errors."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def household_number(value: str) -> str:
    """Only modern OEM household IDs; legacy/commercial formats need other flows."""
    if not isinstance(value, str) or not re.fullmatch(r"572[0-9]{9}", value.strip()):
        raise AccountLinkError("link_invalid_number")
    return value.strip()


def parse_lookup(payload, number: str) -> PersonalAccount:
    """Never pick the first result of an ambiguous lookup."""
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "success"
        or payload.get("error")
    ):
        raise AccountLinkError("link_lookup_failed")
    rows = payload.get("data")
    if rows == []:
        raise AccountLinkError("link_not_found")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise AccountLinkError("link_lookup_failed")
    row = rows[0]
    if (
        row.get("account") != number
        or not isinstance(row.get("address"), str)
        or not row["address"].strip()
    ):
        raise AccountLinkError("link_lookup_failed")
    return PersonalAccount(number, row["address"].strip())


class AccountLinksClientMixin:
    """Use the authenticated transport, with no retry of cabinet mutations."""

    async def lookup_account(self, number: str) -> PersonalAccount:
        number = household_number(number)
        payload = await self._get(
            "/api/billing/joint/info/get_accounts", params={"account": number}
        )
        return parse_lookup(payload, number)

    async def link_account(self, candidate: PersonalAccount, authorize) -> None:
        number = household_number(candidate.number)
        linked = {a.number: a for a in await self.list_accounts()}
        if number in linked:
            raise AccountLinkError("link_already_linked")
        if await self.lookup_account(number) != candidate:
            raise AccountLinkError("link_changed")
        await self._ensure_token()
        authorize()
        await self._change_link("POST", "/add", json={"data": {"account": number}})

    async def unlink_account(self, candidate: PersonalAccount, authorize) -> None:
        number = household_number(candidate.number)
        linked = {a.number: a for a in await self.list_accounts()}
        if number not in linked:
            raise AccountLinkError("link_not_linked")
        if linked[number] != candidate:
            raise AccountLinkError("link_changed")
        await self._ensure_token()
        authorize()
        await self._change_link("DELETE", "/remove", params={"account": number})

    async def _change_link(self, method, path, **kwargs):
        try:
            response = await self._request(method, LINKS + path, **kwargs)
        except AccountAPIChanged:
            raise
        except AccountError:
            raise AccountLinkError("link_uncertain") from None
        if response.status_code == 429:
            raise AccountLinkError("link_rate_limit")
        if 400 <= response.status_code < 500:
            raise AccountLinkError("link_rejected")
        if method == "DELETE" and response.status_code == 204:
            return
        if method == "POST" and 200 <= response.status_code < 300:
            try:
                body = response.json()
            except (ValueError, TypeError):
                body = None
            if (
                isinstance(body, dict)
                and body.get("status") == "success"
                and not body.get("error")
            ):
                return
            if isinstance(body, dict) and body.get("status") == "error":
                raise AccountLinkError("link_rejected")
        # A successful-looking but unexpected body can follow an accepted write.
        raise AccountLinkError("link_uncertain")
