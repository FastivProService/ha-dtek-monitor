"""Local meter references and proposals, independent of DTEK submission outcomes."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import re
import secrets
from time import monotonic

from .account import account_key
from .account_submission import SubmissionError
from .account_zones import SUBMISSION_SCHEMES


def amount(value):
    """Bounded, non-negative decimal kWh; never use binary floating arithmetic."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{1,12}(\.[0-9]{1,9})?", value
    ):
        raise SubmissionError("invalid_energy")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise SubmissionError("invalid_energy") from None


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Missing timezone")
    return result


def estimate(binding, zone, value):
    row = binding["zones"][zone]
    if binding["mode"] == "direct":
        return amount(value)
    return amount(row["anchor_meter"]) + amount(value) - amount(row["anchor_source"])


class EnergyManager:
    """All mutations are serialized; references survive restarts and failed POSTs.

    read_source is synchronous: it returns a snapshot of HA's current state.
    Tokens are server-held, short-lived, user/account/revision-bound snapshots.
    """

    def __init__(self, store, provider, read_source, now=None):
        self.store, self.provider, self.read_source = store, provider, read_source
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._data = None
        self._lock = asyncio.Lock()
        self._tokens = OrderedDict()
        self._closed = False
        self._failed = False

    def key(self, number):
        return account_key(self.provider, number)

    async def _load(self):
        if self._closed:
            raise SubmissionError("not_available")
        if self._failed:
            raise SubmissionError("energy_store_unavailable")
        if self._data is not None:
            return
        try:
            data = await self.store.async_load()
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("Invalid energy references")
            for binding in data.values():
                if not (
                    isinstance(binding["revision"], str)
                    and binding["mode"] in ("direct", "offset")
                    and binding.get("freshness", "reported")
                    in ("reported", "availability")
                    and isinstance(binding["serial"], str)
                    and type(binding["digits"]) is int
                    and 1 <= binding["digits"] <= 8
                    and type(binding["max_age"]) is int
                    and 60 <= binding["max_age"] <= 86400
                    and 1 <= amount(binding["max_power_kw"]) <= 1000
                    and set(binding["zones"]) in SUBMISSION_SCHEMES
                    and isinstance(binding["history"], list)
                    and len(binding["history"]) <= 24
                ):
                    raise ValueError("Invalid energy binding")
                for row in binding["zones"].values():
                    if not (
                        isinstance(row["entity_id"], str)
                        and isinstance(row["fingerprint"], str)
                        and row["unit"] in ("Wh", "kWh", "MWh")
                        and row["fault"]
                        in (None, "source_reset", "source_changed", "source_jump")
                    ):
                        raise ValueError("Invalid energy source")
                    amount(row["high_water"])
                    instant(row["observed_at"])
                    if binding["mode"] == "offset":
                        amount(row["anchor_meter"])
                        amount(row["anchor_source"])
            self._data = data
        except Exception:
            self._failed = True
            raise SubmissionError("energy_store_unavailable") from None

    async def _save(self, data):
        try:
            await self.store.async_save(data)
        except BaseException:
            # A write may have reached disk. Stop using old in-memory references.
            self._failed = True
            raise
        self._data = data

    async def _persist(self, data):
        try:
            await self._save(data)
        except Exception:
            raise SubmissionError("energy_store_unavailable") from None

    async def close(self):
        async with self._lock:
            self._closed = True
            self._tokens.clear()

    async def start(self):
        async with self._lock:
            await self._load()

    def uses(self, entity_id):
        return not self._closed and any(
            row["entity_id"] == entity_id
            for binding in [
                *(self._data or {}).values(),
                *(d["binding"] for d in self._tokens.values() if d["kind"] == "change"),
            ]
            for row in binding["zones"].values()
        )

    def _sample(self, entity_id, max_age, authorize, freshness="reported"):
        authorize(entity_id)
        sample = self.read_source(entity_id)
        age = (self.now() - instant(sample["at"])).total_seconds()
        # Availability is an explicit contract with the source integration: it
        # must mark a disconnected device unavailable. Do not invent a newer
        # sample timestamp or infer freshness from other sensors of the device.
        if age < -5 or (freshness == "reported" and age > max_age):
            raise SubmissionError("source_stale")
        amount(sample["value"])
        return sample

    def _track(self, binding, row, sample):
        """Latch discontinuities: a later high value cannot silently repair a reset."""
        if row["fault"]:
            return False
        fault = None
        value, previous = amount(sample["value"]), amount(row["high_water"])
        elapsed = (instant(sample["at"]) - instant(row["observed_at"])).total_seconds()
        if sample["fingerprint"] != row["fingerprint"] or sample["unit"] != row["unit"]:
            fault = "source_changed"
        elif value < previous:
            fault = "source_reset"
        elif (
            value - previous
            > amount(binding["max_power_kw"]) * Decimal(str(max(0, elapsed))) / 3600 + 1
        ):
            fault = "source_jump"
        if fault:
            row["fault"] = fault
            binding["revision"] = secrets.token_hex(16)
            return True
        if value > previous:
            row.update(high_water=sample["value"], observed_at=sample["at"])
            return True
        return False

    async def observe(self, entity_id, sample):
        """Called with the event's snapshot, not a later value from hass.states."""
        async with self._lock:
            await self._load()
            data = deepcopy(self._data)
            changed = False
            for binding in data.values():
                for row in binding["zones"].values():
                    if row["entity_id"] == entity_id:
                        changed |= self._track(binding, row, sample)
            for draft in self._tokens.values():
                if draft["kind"] == "change":
                    for row in draft["binding"]["zones"].values():
                        if row["entity_id"] == entity_id:
                            self._track(draft["binding"], row, sample)
            if changed:
                await self._persist(data)

    async def _view(self, number, authorize):
        binding = deepcopy(self._data.get(self.key(number)))
        if binding is None:
            return {"binding": None}
        # Enforce access before exposing even names/history of configured sources.
        for row in binding["zones"].values():
            authorize(row["entity_id"])
        readings = {}
        changed = False
        for zone, row in binding["zones"].items():
            try:
                sample = self._sample(
                    row["entity_id"],
                    binding["max_age"],
                    authorize,
                    binding.get("freshness", "reported"),
                )
                changed |= self._track(binding, row, sample)
                if row["fault"]:
                    raise SubmissionError(row["fault"])
                value = estimate(binding, zone, sample["value"])
                if value < 0 or value >= 10 ** binding["digits"]:
                    raise SubmissionError("invalid_energy")
                readings[zone] = {
                    "value": str(value),
                    "send_value": str(value.to_integral_value(rounding=ROUND_FLOOR)),
                    "source": sample,
                }
            except SubmissionError as error:
                if error.code == "unauthorized":
                    raise
                readings[zone] = {"error": error.code}
        if changed:
            await self._persist({**self._data, self.key(number): binding})
        return {
            "binding": binding,
            "readings": readings,
            "ready": all("error" not in row for row in readings.values()),
        }

    def _token(self, kind, number, user, data):
        now = monotonic()
        self._tokens = OrderedDict(
            (k, v) for k, v in self._tokens.items() if v["expires"] > now
        )
        while len(self._tokens) >= 32:
            self._tokens.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = {
            "kind": kind,
            "number": number,
            "user": user,
            "expires": now + 180,
            **data,
        }
        return token

    def _lookup(self, token, kind, number, user):
        draft = self._tokens.get(token)
        if (
            not draft
            or draft["kind"] != kind
            or draft["number"] != number
            or draft["user"] != user
            or draft["expires"] <= monotonic()
        ):
            raise SubmissionError("energy_confirmation_expired")
        current = self._data.get(self.key(number))
        if (current or {}).get("revision") != draft["revision"]:
            raise SubmissionError("energy_changed")
        return draft

    async def summary(self, number):
        """Local scheduled display only; no interactive confirmation token."""
        async with self._lock:
            await self._load()
            return await self._view(number, lambda *_: None)

    async def get(self, number, user, authorize):
        async with self._lock:
            await self._load()
            result = await self._view(number, authorize)
            if result.get("ready"):
                result["proposal"] = self._token(
                    "proposal",
                    number,
                    user,
                    {
                        "revision": result["binding"]["revision"],
                        "readings": result["readings"],
                    },
                )
            return result

    async def preview(
        self, number, user, context, config, references, revision, authorize
    ):
        """context/config are supplied only for a new or replaced binding."""
        async with self._lock:
            await self._load()
            old = self._data.get(self.key(number))
            if (old or {}).get("revision") != revision:
                raise SubmissionError("energy_changed")
            if config is None:
                if old is None or old["mode"] != "offset":
                    raise SubmissionError("invalid_energy")
                binding = deepcopy(old)
            else:
                if not context or not isinstance(config, dict):
                    raise SubmissionError("invalid_energy")
                entities = config.get("entities")
                zones = {z["zone"] for z in context["zones"]}
                if (
                    config.get("mode") not in ("direct", "offset")
                    or config.get("freshness", "reported")
                    not in ("reported", "availability")
                    or not isinstance(entities, dict)
                    or set(entities) != zones
                    or len(set(entities.values())) != len(zones)
                    or type(config.get("max_age")) is not int
                    or not 60 <= config["max_age"] <= 86400
                    or not 1 <= amount(config.get("max_power_kw")) <= 1000
                ):
                    raise SubmissionError("invalid_energy")
                binding = {
                    "mode": config["mode"],
                    "freshness": config.get("freshness", "reported"),
                    "serial": context["serial"],
                    "digits": context["digits"],
                    "max_age": config["max_age"],
                    "max_power_kw": config["max_power_kw"],
                    "zones": {z: {"entity_id": e} for z, e in entities.items()},
                    "history": deepcopy((old or {}).get("history", [])),
                }
            if binding["mode"] == "offset" and (
                not isinstance(references, dict)
                or set(references) != set(binding["zones"])
            ):
                raise SubmissionError("invalid_energy")
            changes = {}
            for zone, row in binding["zones"].items():
                sample = self._sample(
                    row["entity_id"],
                    binding["max_age"],
                    authorize,
                    binding.get("freshness", "reported"),
                )
                reference = None
                if config is None and (
                    row["fingerprint"] != sample["fingerprint"]
                    or row["unit"] != sample["unit"]
                ):
                    raise SubmissionError("source_changed")
                if binding["mode"] == "offset":
                    reference = amount(references[zone])
                    if reference >= 10 ** binding["digits"]:
                        raise SubmissionError("invalid_energy")
                previous = None
                if old and old["serial"] == binding["serial"] and zone in old["zones"]:
                    old_row = old["zones"][zone]
                    if (
                        old_row["fingerprint"] == sample["fingerprint"]
                        and not old_row["fault"]
                        and amount(sample["value"]) >= amount(old_row["high_water"])
                    ):
                        previous = estimate(old, zone, sample["value"])
                changes[zone] = {
                    "source": sample["value"],
                    "source_at": sample["at"],
                    "actual": str(reference) if reference is not None else None,
                    "previous_estimate": (
                        str(previous) if previous is not None else None
                    ),
                    "correction": (
                        str(reference - previous)
                        if reference is not None and previous is not None
                        else None
                    ),
                }
                row.update(
                    fingerprint=sample["fingerprint"],
                    unit=sample["unit"],
                    anchor_source=sample["value"],
                    anchor_meter=str(reference) if reference is not None else None,
                    high_water=sample["value"],
                    observed_at=sample["at"],
                    fault=None,
                )
            binding["revision"] = secrets.token_hex(16)
            record = {
                "id": binding["revision"],
                "at": self.now().isoformat(),
                "kind": "binding" if config is not None else "calibration",
                "changes": changes,
            }
            binding["history"] = [*binding["history"], record][-24:]
            token = self._token(
                "change", number, user, {"revision": revision, "binding": binding}
            )
            return {"confirmation": token, "binding": binding, "change": record}

    async def save(self, number, user, token, authorize):
        async with self._lock:
            await self._load()
            draft = self._lookup(token, "change", number, user)
            binding = deepcopy(draft["binding"])
            for row in binding["zones"].values():
                sample = self._sample(
                    row["entity_id"],
                    binding["max_age"],
                    authorize,
                    binding.get("freshness", "reported"),
                )
                self._track(binding, row, sample)
                if row["fault"]:
                    raise SubmissionError(row["fault"])
            # Rechecking a live sample never moves the captured anchor pair.
            await self._persist({**self._data, self.key(number): binding})
            del self._tokens[token]
            return {"saved": True}

    async def validate_proposal(self, number, user, token, context, values, authorize):
        async with self._lock:
            await self._load()
            draft = self._lookup(token, "proposal", number, user)
            view = await self._view(number, authorize)
            self._lookup(token, "proposal", number, user)
            binding = view["binding"]
            if (
                not view.get("ready")
                or binding["serial"] != context["serial"]
                or set(binding["zones"]) != {z["zone"] for z in context["zones"]}
            ):
                raise SubmissionError("energy_changed")
            if values != {z: row["send_value"] for z, row in draft["readings"].items()}:
                raise SubmissionError("energy_changed")
            return {"mode": binding["mode"], "readings": deepcopy(draft["readings"])}

    def guard_proposal(self, number, user, token, authorize):
        """No await: recheck immediately before the transport's single POST."""
        if self._closed or self._failed or self._data is None:
            raise SubmissionError("energy_store_unavailable")
        self._lookup(token, "proposal", number, user)
        binding = deepcopy(self._data[self.key(number)])
        for row in binding["zones"].values():
            sample = self._sample(
                row["entity_id"],
                binding["max_age"],
                authorize,
                binding.get("freshness", "reported"),
            )
            self._track(binding, row, sample)
            if row["fault"]:
                raise SubmissionError(row["fault"])

    async def remove(self, number, revision, authorize):
        async with self._lock:
            await self._load()
            binding = self._data.get(self.key(number))
            if not binding or binding["revision"] != revision:
                raise SubmissionError("energy_changed")
            for row in binding["zones"].values():
                authorize(row["entity_id"])
            data = {k: v for k, v in self._data.items() if k != self.key(number)}
            await self._persist(data)
            return {"removed": True}
