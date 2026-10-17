"""Finite, source-bound schedule snapshots for display, never live evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from .schedule import ScheduleWindow

PROVIDER_ID = "dtek_oem"
SNAPSHOT_DAYS = 8


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Snapshot timestamps must include a timezone")
    return parsed


@dataclass(frozen=True)
class ScheduleSnapshot:
    provider_id: str
    address: tuple[str, str, str]
    groups: tuple[str, ...]
    checked_at: datetime
    source_updated_at: str
    coverage_start: datetime
    coverage_end: datetime
    windows: tuple[ScheduleWindow, ...]
    contract_revision: str | None = None

    @property
    def snapshot_id(self) -> str:
        """Content identity excludes successful rechecks of unchanged data."""
        payload = self.to_dict()
        payload.pop("checked_at")
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            **(
                {"contract_revision": self.contract_revision}
                if self.contract_revision is not None
                else {}
            ),
            "provider_id": self.provider_id,
            "address": list(self.address),
            "groups": list(self.groups),
            "checked_at": self.checked_at.isoformat(),
            "source_updated_at": self.source_updated_at,
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "windows": [
                {
                    "event_type": w.event_type,
                    "start": w.start.isoformat(),
                    "end": w.end.isoformat(),
                    "summary": w.summary,
                    "description": w.description,
                }
                for w in self.windows
            ],
        }

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        provider_id: str,
        address: tuple[str, str, str],
        now: datetime | None = None,
    ) -> ScheduleSnapshot:
        """Reject corrupt, foreign and unbounded cache data."""
        if not isinstance(value, dict) or value.get("provider_id") != provider_id:
            raise ValueError("Snapshot provider mismatch")
        if value.get("address") != list(address):
            raise ValueError("Snapshot address mismatch")
        groups = value["groups"]
        if (
            not isinstance(groups, list)
            or not groups
            or any(not isinstance(g, str) or not g.strip() for g in groups)
            or groups != sorted(set(groups))
        ):
            raise ValueError("Invalid snapshot groups")
        checked = _time(value["checked_at"])
        start, end = _time(value["coverage_start"]), _time(value["coverage_end"])
        if not start <= checked < end or end - start > timedelta(
            days=SNAPSHOT_DAYS + 1
        ):
            raise ValueError("Invalid snapshot coverage")
        if checked > (now or datetime.now(timezone.utc)) + timedelta(minutes=5):
            raise ValueError("Snapshot check time is in the future")
        if not isinstance(value["source_updated_at"], str):
            raise ValueError("Invalid source version")
        revision = value.get("contract_revision")
        if revision is not None and (not isinstance(revision, str) or not revision):
            raise ValueError("Invalid contract revision")
        records = value["windows"]
        if not isinstance(records, list) or len(records) > 10000:
            raise ValueError("Invalid snapshot windows")
        windows = []
        for record in records:
            w = ScheduleWindow(
                record["event_type"],
                _time(record["start"]),
                _time(record["end"]),
                record["summary"],
                record["description"],
            )
            if (
                w.event_type not in ("outage", "possible")
                or not start <= w.start < w.end <= end
                or not isinstance(w.summary, str)
                or not isinstance(w.description, str)
                or w.description.partition("; group=")[2] not in groups
            ):
                raise ValueError("Invalid snapshot event or group provenance")
            windows.append(w)
        return cls(
            provider_id,
            address,
            tuple(groups),
            checked,
            value["source_updated_at"],
            start,
            end,
            tuple(windows),
            revision,
        )

    def events_between(self, start: datetime, end: datetime) -> list[ScheduleWindow]:
        """Return only stored concrete events; never extrapolate a weekly template."""
        return [w for w in self.windows if w.end > start and w.start < end]
