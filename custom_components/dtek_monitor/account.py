"""Read-only household account contract, independent of outage monitoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Any, Protocol

CONF_ENTRY_KIND = "entry_kind"
ENTRY_ACCOUNT = "account"
CONF_ACCOUNTS = "accounts"
CONF_ACCOUNT_SUBJECT = "account_subject"
ACCOUNT_UPDATE_HOURS = 6
ACCOUNT_PROVIDERS = frozenset({"dtek_oem"})


class AccountError(Exception):
    """Account service unavailable or its response cannot be trusted."""


class AccountAuthError(AccountError):
    """User action is required to authenticate the account."""


class AccountRateLimitError(AccountError):
    """The provider asked the client to stop requesting data."""


@dataclass(frozen=True)
class PersonalAccount:
    number: str = field(repr=False)
    address: str = field(repr=False)

    @property
    def label(self) -> str:
        return f"••••{self.number[-4:]} — {self.address}"


@dataclass(frozen=True)
class Reading:
    zone: str
    value: Decimal
    read_at: date


@dataclass(frozen=True)
class AccountSnapshot:
    model: str | None
    verification_year: int | None
    readings: tuple[Reading, ...]
    consumption: Decimal | None
    consumption_period: str | None
    fetched_at: datetime


class AccountProvider(Protocol):
    """Authenticated API; implementations must never perform billing writes."""

    async def authenticate(self, phone: str, password: str) -> str: ...
    async def list_accounts(self) -> tuple[PersonalAccount, ...]: ...
    async def read_account(self, number: str) -> AccountSnapshot: ...
    async def close(self) -> None: ...


def account_key(provider_id: str, value: str) -> str:
    """Stable private identifiers, independent of display names and passwords."""
    return hashlib.sha256(f"{provider_id}:{value}".encode()).hexdigest()[:32]


def normalize_phone(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected Ukrainian phone number")
    phone = re.sub(r"[\s()+-]", "", value)
    if not re.fullmatch(r"380\d{9}", phone):
        raise ValueError("Expected Ukrainian phone number")
    return "+" + phone


def decimal_value(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise AccountError("Invalid numeric account data")
    try:
        result = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise AccountError("Invalid numeric account data") from None
    if not result.is_finite():
        raise AccountError("Invalid numeric account data")
    return result


def parse_readings(payload: Any, today: date) -> tuple[Reading, ...]:
    """Use only explicitly unblocked previous readings, never pending input.

    MVP supports one meter per household account. Fail visibly for a different
    shape instead of combining meters or zones with incompatible timestamps.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise AccountError("Invalid meter readings response")
    meters = payload["data"]
    if not meters:
        return ()
    if len(meters) != 1 or payload.get("totalPages", 1) != 1:
        raise AccountError("Multiple meters require separate account support")
    meter = meters[0]
    if not isinstance(meter, dict) or not isinstance(meter.get("readings"), dict):
        raise AccountError("Invalid meter readings response")
    scales = meter["readings"].get("activeEnergy", [])
    if not isinstance(scales, list):
        raise AccountError("Invalid meter reading scales")
    result = []
    zones = set()
    for scale in scales:
        if not isinstance(scale, dict):
            raise AccountError("Invalid meter reading scale")
        previous = scale.get("previous")
        if previous is None:
            continue
        if not isinstance(previous, dict):
            raise AccountError("Invalid previous reading")
        if previous.get("isBlocked") is not False:
            continue
        zone = scale.get("deviceType")
        if not isinstance(zone, str) or not re.fullmatch(r"[A-Z_0-9]{1,40}", zone):
            raise AccountError("Invalid meter zone")
        if zone in zones:
            raise AccountError("Duplicate meter zone")
        try:
            reading_date = date.fromisoformat(previous["readAt"])
        except (KeyError, TypeError, ValueError):
            raise AccountError("Invalid reading date") from None
        value = decimal_value(previous.get("value"))
        if reading_date > today or value < 0:
            raise AccountError("Invalid previous meter reading")
        zones.add(zone)
        result.append(Reading(zone, value, reading_date))
    return tuple(sorted(result, key=lambda item: item.zone))


def parse_consumption(payload: Any, today: date) -> tuple[Decimal | None, str | None]:
    """Keep signed corrections and the reporting month; never fabricate zero."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise AccountError("Invalid consumption response")
    periods: dict[str, Decimal] = {}
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise AccountError("Invalid consumption item")
        period = item.get("date")
        if not isinstance(period, str) or not re.fullmatch(r"\d{4}-\d{2}", period):
            raise AccountError("Invalid consumption period")
        try:
            start = date.fromisoformat(period + "-01")
        except ValueError:
            raise AccountError("Invalid consumption period") from None
        if period in periods:
            raise AccountError("Duplicate consumption period")
        value = decimal_value(item.get("consumption"))
        if start < today.replace(day=1):
            periods[period] = value
    if not periods:
        return None, None
    period = max(periods)
    return periods[period], period
