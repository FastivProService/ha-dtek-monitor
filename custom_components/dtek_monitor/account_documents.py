"""Account-scoped read-only technical acts from the official legacy cabinet."""

import asyncio
import base64
import binascii
from datetime import datetime, timedelta
import hashlib
import json
import logging
import re
from time import monotonic

from curl_cffi.requests.exceptions import RequestException
from homeassistant.components import persistent_notification as pn
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .account import AccountAPIChanged, AccountAuthError, AccountError, AccountRateLimitError, account_key
from .account_group_client import AccountGroupClient
from .account_api_health import clear_api_issue, report_api_issue
from .account_notifications import settings
from .account_submission import SubmissionError
from .localization import is_ukrainian
from .schedule import KYIV_TZ

INTERFACE = "account_documents"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * ((MAX_DOCUMENT_BYTES + 2) // 3) + 65536
MAX_DOCUMENTS = 1000
_LOGGER = logging.getLogger(__name__)


def changed():
    return AccountAPIChanged(INTERFACE, "response_format")


def parse_documents(payload, number):
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise changed()
    if payload.get("data") == "Empty result":
        return []
    data = payload.get("data")
    groups = data.get("itemsByType") if isinstance(data, dict) else None
    if not isinstance(groups, dict) or len(groups) > 100:
        raise changed()
    rows, seen = [], set()
    for kind, items in groups.items():
        if not isinstance(kind, str) or not re.fullmatch(r"[0-9]{1,5}", kind) or not isinstance(items, list):
            raise changed()
        for item in items:
            if not isinstance(item, dict):
                raise changed()
            identity, name = item.get("actId"), item.get("fileName")
            if (type(identity) not in (str, int) or not 1 <= len(str(identity)) <= 200
                    or not isinstance(name, str) or not 1 <= len(name) <= 250
                    or any(ord(c) < 32 for c in name)):
                raise changed()
            key = account_key(number, str(identity))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"id": key, "name": name.replace("/", "_").replace("\\", "_"), "type": kind, "_act_id": identity})
            if len(rows) > MAX_DOCUMENTS:
                raise changed()
    return rows


def parse_document_file(payload, name):
    if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
        raise changed()
    data = payload["data"]
    encoded, kind = data.get("file"), data.get("docClass")
    if not isinstance(kind, str) or kind.lower() != "pdf":
        raise SubmissionError("document_format")
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_DOCUMENT_BYTES + 2) // 3):
        raise SubmissionError("document_too_large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise changed() from None
    if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-2048:]:
        raise changed()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise SubmissionError("document_too_large")
    return {"name": name if name.lower().endswith(".pdf") else name + ".pdf", "mime_type": "application/pdf", "data": encoded,
            "sha256": hashlib.sha256(raw).hexdigest()}


class DocumentClient(AccountGroupClient):
    async def request_document(self, number, path, act_id=None):
        if path not in ("/tech_acts/get_acts_list", "/tech_acts/get_act"):
            raise ValueError("Unsupported document route")
        if number not in self.modern._accounts:
            raise SubmissionError("not_available")
        async with self._lock:
            for attempt in range(2):
                response = None
                try:
                    if number not in self._tokens:
                        await self._login(number)
                    token = self._tokens[number]
                    body = {"token": token, "account": number, "userType": "person", "url": path}
                    if act_id is not None:
                        body["actId"] = act_id
                    response = await self._session.request("POST", self.spec.group_origin + "/api/get-common", json=body,
                        headers={"Authorization": "Bearer " + token, "Origin": self.spec.group_origin, "Referer": self.spec.group_origin + "/"},
                        allow_redirects=False, stream=True)
                    if response.status_code in (401, 403):
                        raise AccountAuthError("Document session expired")
                    if response.status_code in (404, 410):
                        raise AccountAPIChanged(INTERFACE, f"HTTP {response.status_code}")
                    if response.status_code == 429:
                        raise AccountRateLimitError("Document request limited")
                    if response.status_code != 200:
                        raise AccountError("Documents unavailable")
                    buffer = bytearray()
                    async for chunk in response.aiter_content():
                        buffer.extend(chunk)
                        if len(buffer) > MAX_RESPONSE_BYTES:
                            raise SubmissionError("document_too_large")
                    try:
                        payload = json.loads(buffer)
                    except (ValueError, UnicodeError):
                        raise changed() from None
                    if isinstance(payload, dict) and payload.get("status") in ("fail", "error"):
                        raise AccountError("Document request rejected")
                    return payload
                except AccountAuthError:
                    self._tokens.pop(number, None)
                    if attempt:
                        raise AccountError("Document session unavailable") from None
                except AccountAPIChanged as error:
                    raise AccountAPIChanged(INTERFACE, error.reason) from None
                except (RequestException, TimeoutError, OSError):
                    raise AccountError("Documents unavailable") from None
                finally:
                    if response is not None:
                        await response.aclose()

    async def list_documents(self, number):
        await self.modern.list_accounts()
        return parse_documents(await self.request_document(number, "/tech_acts/get_acts_list"), number)

    async def download(self, number, row):
        await self.modern.list_accounts()
        return parse_document_file(await self.request_document(number, "/tech_acts/get_act", row["_act_id"]), row["name"])


class AccountDocuments:
    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.client = None
        self.store = Store(hass, 1, f"dtek_monitor.documents.{entry.entry_id}")
        self.records = None
        self.cache = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._unsub = None
        self._task = None

    async def start(self):
        self._unsub = async_track_time_interval(self.hass, self._schedule, timedelta(minutes=30))
        self._schedule()

    @callback
    def _schedule(self, now=None):
        if not self._closed and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_task(self.poll())

    async def poll(self):
        for number in list(self.entry.data.get("accounts", {})):
            if self._closed:
                return
            if settings(self.entry, number)["documents"]:
                try:
                    await self.get(number)
                except AccountError:
                    _LOGGER.debug("DTEK document check unavailable")

    def authorize(self, number):
        if self._closed or number not in self.entry.data.get("accounts", {}) or number not in self.coordinator.client._accounts:
            raise SubmissionError("not_available")

    async def _load(self):
        if self.records is None:
            try:
                data = await self.store.async_load()
                if data is not None and (not isinstance(data, dict) or any(
                        not isinstance(row, dict) or any(
                            not isinstance(row.get(field, []), list)
                            or not all(isinstance(k, str) and re.fullmatch(r"[a-f0-9]{32}", k) for k in row.get(field, []))
                            or len(row.get(field, [])) > MAX_DOCUMENTS * 2
                            for field in ("seen", "pending"))
                        or "seen" not in row for row in data.values())):
                    raise ValueError
                self.records = data or {}
            except (ValueError, TypeError, OSError):
                raise SubmissionError("journal_unavailable") from None

    async def get(self, number):
        async with self._lock:
            self.authorize(number)
            await self._load()
            key = account_key(self.entry.data["provider_id"], number)
            prior = self.cache.get(key)
            if prior and monotonic() - prior["at"] < 60:
                return prior["public"]
            try:
                if self.client is None:
                    self.client = DocumentClient(self.coordinator.client)
                rows = await self.client.list_documents(number)
            except AccountAPIChanged as error:
                report_api_issue(self.hass, self.entry.data["provider_id"], error, scope=self.entry.entry_id)
                raise
            self.authorize(number)
            clear_api_issue(self.hass, self.entry.data["provider_id"], INTERFACE, scope=self.entry.entry_id)
            old = self.records.get(key)
            known = set(old["seen"]) if old else set()
            new = [r for r in rows if r["id"] not in known] if old else []
            # Save the baseline first; polling errors never become an empty document list.
            updated = {k:v for k,v in self.records.items() if k in {account_key(self.entry.data['provider_id'], n) for n in self.entry.data.get('accounts', {})}}
            ids = list(dict.fromkeys([*([r['id'] for r in rows]), *(old['seen'] if old else [])]))[:MAX_DOCUMENTS * 2]
            updated[key] = {"seen": ids, "pending": list(dict.fromkeys([*(old.get('pending', []) if old else []), *[r['id'] for r in new]]))}
            try:
                await self.store.async_save(updated)
            except OSError:
                raise SubmissionError("journal_unavailable") from None
            self.records = updated
            if settings(self.entry, number)["documents"] and updated[key]["pending"]:
                uk = is_ukrainian(self.hass.config.language)
                pn.async_create(self.hass,
                    "У кабінеті з’явилися нові документи. Відкрийте «Мій ДТЕК → Покази → Документи ДТЕК»." if uk else
                    "New documents are available. Open My DTEK → Readings → DTEK documents.",
                    ("ДТЕК · новий документ" if uk else "DTEK · new document") + f" · ••••{number[-4:]}",
                    f"dtek_documents_{self.entry.entry_id}_{key}")
            # Stable notification IDs prevent duplicates if persisting delivery fails.
            self.records[key]["pending"] = []
            try:
                await self.store.async_save(self.records)
            except OSError:
                self.records = None
                raise SubmissionError("journal_unavailable") from None
            public = {"documents": [{k:v for k,v in r.items() if not k.startswith('_')} for r in rows],
                      "checked_at": datetime.now(KYIV_TZ).isoformat(), "notifications": settings(self.entry, number)["documents"]}
            self.cache[key] = {"at": monotonic(), "rows": rows, "public": public}
            return public

    async def download(self, number, identity):
        await self.get(number)
        self.authorize(number)
        key = account_key(self.entry.data["provider_id"], number)
        row = next((r for r in self.cache.get(key, {}).get("rows", []) if r["id"] == identity), None)
        if row is None:
            raise SubmissionError("document_missing")
        self.authorize(number)
        result = await self.client.download(number, row)
        self.authorize(number)
        return result

    async def close(self):
        self._closed = True
        if self._unsub:
            self._unsub()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.cache.clear()
        if self.client:
            await self.client.close()


async def remove_account_documents(hass, entry):
    await Store(hass, 1, f"dtek_monitor.documents.{entry.entry_id}").async_remove()
    for number in entry.data.get("accounts", {}):
        key = account_key(entry.data["provider_id"], number)
        pn.async_dismiss(hass, f"dtek_documents_{entry.entry_id}_{key}")
    clear_api_issue(hass, entry.data["provider_id"], INTERFACE, scope=entry.entry_id)
