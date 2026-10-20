"""Explicit, single-attempt household readings with a durable ambiguity barrier."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re
import secrets
from time import monotonic
from zoneinfo import ZoneInfo

from .account import AccountError, account_key, decimal_value
from .account_zones import SUBMISSION_SCHEMES

KYIV = ZoneInfo("Europe/Kyiv")


class SubmissionError(AccountError):
    """Stable public reason, without upstream text or account identifiers."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def submission_context(details, payload, number, today: date):
    """Conservative initial scope: one connected consumption meter, 1/2 zones."""
    if (
        not isinstance(details, dict)
        or details.get("accountNumber") != number
        or not isinstance(payload, dict)
        or payload.get("totalPages") != 1
        or not isinstance(payload.get("data"), list)
        or len(payload["data"]) != 1
    ):
        raise SubmissionError("unsupported_meter")
    meter = payload["data"][0]
    if not isinstance(meter, dict):
        raise SubmissionError("unsupported_meter")
    serial = meter.get("serialNumber")
    if (
        not isinstance(serial, str)
        or not 1 <= len(serial) <= 80
        or serial != details.get("serialNum")
        or meter.get("isInputReadingsBlocked") is not False
        or meter.get("isGreenTariff") is not False
        or "disconnectionDate" not in meter
        or meter["disconnectionDate"] is not None
    ):
        raise SubmissionError("unsupported_meter")
    digits = details.get("digits")
    if not re.fullmatch(r"0?[1-8]", str(digits)):
        raise SubmissionError("unsupported_meter")
    digits = int(digits)
    address = details.get("address")
    if not isinstance(address, str) or not address.strip() or len(address) > 500:
        raise SubmissionError("unsupported_meter")
    readings = meter.get("readings")
    if not isinstance(readings, dict) or any(
        v for k, v in readings.items() if k != "activeEnergy"
    ):
        raise SubmissionError("unsupported_meter")
    scales = readings.get("activeEnergy")
    if not isinstance(scales, list) or not 1 <= len(scales) <= 2:
        raise SubmissionError("unsupported_meter")
    zones = []
    for scale in scales:
        if not isinstance(scale, dict):
            raise SubmissionError("unsupported_meter")
        if not isinstance(scale.get("deviceType"), str):
            raise SubmissionError("unsupported_meter")
        previous = scale.get("previous")
        if (
            scale.get("current") is not None
            or "current" not in scale
            or scale.get("isIrrelevant") not in (None, False)
            or not isinstance(previous, dict)
            or previous.get("isBlocked") is not False
        ):
            raise SubmissionError("pending_or_blocked")
        try:
            day = date.fromisoformat(previous["readAt"])
        except (KeyError, ValueError, TypeError):
            raise SubmissionError("unsupported_meter") from None
        value = decimal_value(previous.get("value"))
        if day > today or value < 0 or value >= 10**digits:
            raise SubmissionError("unsupported_meter")
        zones.append(
            {
                "zone": scale.get("deviceType"),
                "previous": str(value),
                "date": day.isoformat(),
            }
        )
    if {z["zone"] for z in zones} not in SUBMISSION_SCHEMES or len(
        {z["zone"] for z in zones}
    ) != len(zones):
        raise SubmissionError("unsupported_meter")
    context = {
        "address": address,
        "serial": serial,
        "digits": digits,
        "date": today.isoformat(),
        "zones": sorted(zones, key=lambda z: z["zone"]),
    }
    context["revision"] = digest(context)
    return context


def validate_values(context, values):
    if not isinstance(values, dict) or set(values) != {
        z["zone"] for z in context["zones"]
    }:
        raise SubmissionError("invalid_readings")
    result = []
    for zone in context["zones"]:
        value = values[zone["zone"]]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,8}", value):
            raise SubmissionError("invalid_readings")
        numeric = int(value)
        if numeric >= 10 ** context["digits"] or numeric < Decimal(zone["previous"]):
            raise SubmissionError("invalid_readings")
        result.append(
            {
                "zone": zone["zone"],
                "value": numeric,
                "previous": zone["previous"],
                "increase": str(Decimal(numeric) - Decimal(zone["previous"])),
            }
        )
    return result


def submission_result(payload, count):
    """A 2xx alone is not success; require an explicit result for every scale."""
    unknown = {"status": "unknown"}
    if not isinstance(payload, list) or len(payload) != count:
        return unknown
    seen = set()
    statuses = []
    for item in payload:
        if not isinstance(item, dict):
            return unknown
        index, status = item.get("index"), item.get("status")
        if (
            type(index) is not int
            or not 0 <= index < count
            or index in seen
            or status not in ("SUCCESS", "ERROR")
        ):
            return unknown
        seen.add(index)
        statuses.append(status)
    return {
        "status": (
            "accepted"
            if all(s == "SUCCESS" for s in statuses)
            else "rejected"
            if all(s == "ERROR" for s in statuses)
            else "partial"
        )
    }


class SubmissionManager:
    """Drafts are short-lived and user-bound. An attempted POST is never replayed."""

    def __init__(self, client, store, provider, energy=None):
        self.client, self.store, self.provider = client, store, provider
        self.energy = energy
        self._lock = asyncio.Lock()
        self._journal = None
        self._drafts = OrderedDict()
        self._closed = False

    async def close(self):
        self._closed = True
        async with self._lock:
            self._drafts.clear()

    async def _load(self):
        if self._closed:
            raise SubmissionError("not_available")
        if self._journal is None:
            try:
                data = await self.store.async_load()
            except Exception:
                raise SubmissionError("journal_unavailable") from None
            if data is not None and (
                not isinstance(data, dict)
                or any(
                    not isinstance(v, dict)
                    or v.get("status")
                    not in (
                        "pending",
                        "unknown",
                        "partial",
                        "accepted",
                        "rejected",
                        "not_sent",
                        "acknowledged",
                    )
                    for v in data.values()
                )
            ):
                raise SubmissionError("journal_unavailable")
            self._journal = data or {}

    async def _save(self, key, record):
        updated = {**self._journal, key: record}
        try:
            await self.store.async_save(updated)
        except Exception:
            raise SubmissionError("journal_unavailable") from None
        self._journal = updated

    def _key(self, number):
        return account_key(self.provider, number)

    def _record(self, number):
        record = deepcopy(self._journal.get(self._key(number)))
        if record:
            record.pop("signature", None)
            if record["status"] == "pending":
                record["status"] = "unknown"
        return record

    def _unresolved(self, number):
        record = self._journal.get(self._key(number), {})
        return record.get("status") in ("pending", "unknown", "partial")

    async def last_attempt(self, number):
        """Read-only journal access; never acknowledge an uncertain delivery."""
        async with self._lock:
            await self._load()
            record = self._record(number)
            if record:
                # The read-only view does not need source bindings or tokens.
                record.pop("source", None)
            return record

    async def context(self, number, authorize):
        async with self._lock:
            await self._load()
            authorize()
            record = self._record(number)
            if self._unresolved(number):
                return {"blocked": True, "last_attempt": record}
            try:
                context = await self.client.read_submission_context(number)
            except SubmissionError as error:
                if error.code not in ("pending_or_blocked", "unsupported_meter"):
                    raise
                authorize()
                return {"blocked": True, "reason": error.code, "last_attempt": record}
            authorize()
            return {**context, "blocked": False, "last_attempt": record}

    async def prepare(
        self, number, user, revision, values, authorize, source_token=None
    ):
        async with self._lock:
            await self._load()
            authorize()
            if self._unresolved(number):
                raise SubmissionError("check_cabinet")
            context = await self.client.read_submission_context(number)
            authorize()
            if context["revision"] != revision:
                raise SubmissionError("context_changed")
            readings = validate_values(context, values)
            source = None
            if source_token:
                if self.energy is None:
                    raise SubmissionError("energy_changed")
                source = await self.energy.validate_proposal(
                    number, user, source_token, context, values, authorize
                )
            signature = digest(
                {
                    "serial": context["serial"],
                    "date": context["date"],
                    "readings": {r["zone"]: r["value"] for r in readings},
                }
            )
            last = self._journal.get(self._key(number), {})
            if last.get("status") == "accepted" and last.get("signature") == signature:
                raise SubmissionError("already_sent")
            now = monotonic()
            self._drafts = OrderedDict(
                (k, v) for k, v in self._drafts.items() if v["expires"] > now
            )
            while len(self._drafts) >= 32:
                self._drafts.popitem(last=False)
            token = secrets.token_urlsafe(32)
            self._drafts[token] = {
                "number": number,
                "user": user,
                "context": context,
                "readings": readings,
                "signature": signature,
                "expires": now + 300,
                "source_token": source_token,
            }
            return {
                "confirmation": token,
                "expires_in": 300,
                "serial": context["serial"],
                "date": context["date"],
                "readings": readings,
                "source": source,
            }

    async def check_source(self, number, authorize):
        """Read-only scheduled inspection. Never creates a draft or a POST attempt."""
        async with self._lock:
            await self._load()
            authorize()
            if self._unresolved(number):
                raise SubmissionError("check_cabinet")
            context = await self.client.read_submission_context(number)
            authorize()
            view = await self.energy.summary(number)
            authorize()
            binding = view.get("binding")
            if not binding:
                raise SubmissionError("no_energy_source")
            if not view.get("ready"):
                raise SubmissionError(
                    next(
                        (
                            row["error"]
                            for row in view.get("readings", {}).values()
                            if "error" in row
                        ),
                        "source_unavailable",
                    )
                )
            if binding["serial"] != context["serial"] or set(binding["zones"]) != {
                z["zone"] for z in context["zones"]
            }:
                raise SubmissionError("energy_changed")
            readings = validate_values(
                context,
                {zone: row["send_value"] for zone, row in view["readings"].items()},
            )
            signature = digest(
                {
                    "serial": context["serial"],
                    "date": context["date"],
                    "readings": {r["zone"]: r["value"] for r in readings},
                }
            )
            last = self._journal.get(self._key(number), {})
            if last.get("status") == "accepted" and last.get("signature") == signature:
                raise SubmissionError("already_sent")
            return {
                "binding_revision": binding["revision"],
                "readings": [
                    {
                        **row,
                        "source_entity": binding["zones"][row["zone"]]["entity_id"],
                        "source_at": view["readings"][row["zone"]]["source"]["at"],
                    }
                    for row in readings
                ],
            }

    async def confirm(self, number, user, token, authorize):
        async with self._lock:
            await self._load()
            authorize()
            draft = self._drafts.get(token)
            if (
                not draft
                or draft["number"] != number
                or draft["user"] != user
                or draft["expires"] <= monotonic()
            ):
                raise SubmissionError("confirmation_expired")
            # Consume before any await: every confirmation permits at most one attempt.
            del self._drafts[token]
            if self._unresolved(number):
                raise SubmissionError("check_cabinet")
            context = await self.client.read_submission_context(number)
            authorize()
            if context != draft["context"]:
                raise SubmissionError("context_changed")
            source = None
            if draft.get("source_token"):
                source = await self.energy.validate_proposal(
                    number,
                    user,
                    draft["source_token"],
                    context,
                    {r["zone"]: str(r["value"]) for r in draft["readings"]},
                    authorize,
                )
            last = self._journal.get(self._key(number), {})
            if (
                last.get("status") == "accepted"
                and last.get("signature") == draft["signature"]
            ):
                raise SubmissionError("already_sent")
            record = {
                "id": secrets.token_hex(16),
                "status": "pending",
                "at": datetime.now(KYIV).isoformat(),
                "meter_serial": context["serial"],
                "signature": draft["signature"],
                "readings": draft["readings"],
                "source": source,
            }
            key = self._key(number)
            await self._save(key, record)

            # Storage yields too; recheck authorization immediately before transport.
            def authorize_send():
                authorize()
                if draft.get("source_token"):
                    self.energy.guard_proposal(
                        number, user, draft["source_token"], authorize
                    )

            try:
                authorize_send()
                result = await self.client.submit_readings(
                    number, draft["readings"], authorize_send
                )
            except SubmissionError:
                await self._save(key, {**record, "status": "not_sent"})
                raise
            except AccountError:
                # Includes unknown failures from transports/providers. Never infer rejection.
                result = {"status": "unknown"}
            except asyncio.CancelledError:
                # Durable pending survives shutdown/cancellation and prevents resending.
                raise
            final = {**record, **result}
            try:
                await self._save(key, final)
            except SubmissionError:
                return {**self._record(number), "status": "unknown"}
            return self._record(number)

    async def acknowledge(self, number, attempt, authorize):
        async with self._lock:
            await self._load()
            authorize()
            key = self._key(number)
            record = self._journal.get(key)
            if (
                not record
                or record.get("id") != attempt
                or not self._unresolved(number)
            ):
                raise SubmissionError("context_changed")
            # Explicit acknowledgment unlocks a new draft; never repeats the old request.
            await self._save(key, {**record, "status": "acknowledged"})
            return {"status": "acknowledged"}
