"""Compare verified future schedules, independent of polling and wall-clock ticks."""

from datetime import datetime, timezone
import hashlib
import json


def schedule_record(schedule):
    """Only dated coverage can confirm a schedule change; forecasts cannot."""
    if schedule is None or schedule.state != "current":
        return None
    coverage = sorted({
        (c.group_id, c.start.timestamp(), c.end.timestamp())
        for c in schedule.coverage if c.kind == "outage"
    })
    if not coverage:
        return None
    return {
        "coverage": [list(c) for c in coverage],
        "intervals": [list(i) for i in sorted({
            (i.group_id, i.kind, i.start.timestamp(), i.end.timestamp())
            for i in schedule.intervals if i.source == "daily"
        })],
    }


def _ranges(ranges, now):
    merged = []
    for start, end in sorted((max(start, now), end) for start, end in ranges if end > now):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def _within(record, group, coverage, now):
    """Clip both versions to the same verified range and instant."""
    return {
        kind: _ranges([
            (max(start, a), min(end, b))
            for g, k, start, end in record["intervals"] if g == group and k == kind
            for a, b in coverage if start < b and end > a
        ], now)
        for kind in ("outage", "possible")
    }


def changed_groups(previous, current, now):
    """Ignore lost coverage, elapsed intervals and groups newly assigned to a house.

    A newly published date counts even when it confirms no outages. Missing dates
    never count as cancellation. Group assignments have their own notification.
    """
    if previous is None or current is None:
        return []
    stamp = now.timestamp()
    old_groups = {c[0] for c in previous["coverage"]}
    changed = []
    for group in sorted(old_groups & {c[0] for c in current["coverage"]}):
        old = _ranges([(a, b) for g, a, b in previous["coverage"] if g == group], stamp)
        new = _ranges([(a, b) for g, a, b in current["coverage"] if g == group], stamp)
        if not new:
            continue
        common = _ranges([
            (max(a, c), min(b, d)) for a, b in old for c, d in new if a < d and b > c
        ], stamp)
        if common != new or _within(previous, group, common, stamp) != _within(current, group, common, stamp):
            changed.append(group)
    return changed


def remember(previous, current, now):
    """Retain temporarily missing days for comparison, never for live calendars."""
    if previous is None:
        return current
    stamp = now.timestamp()
    coverage = [c for c in previous["coverage"] if c[2] > stamp]
    intervals = [i for i in previous["intervals"] if i[3] > stamp]
    # Replace only the ranges the new response actually verifies.
    for group, start, end in current["coverage"]:
        kept = []
        for g, kind, a, b in intervals:
            if g != group or b <= start or a >= end:
                kept.append([g, kind, a, b])
            else:
                if a < start:
                    kept.append([g, kind, a, start])
                if b > end:
                    kept.append([g, kind, end, b])
        intervals = kept
    groups = {g for g, _, _ in current["coverage"]}
    coverage = [c for c in coverage if c[0] in groups] + current["coverage"]
    intervals = [i for i in intervals if i[0] in groups] + current["intervals"]
    return {
        "coverage": [[g, a, b] for g in sorted(groups) for a, b in _ranges(
            [(a, b) for key, a, b in coverage if key == g], stamp
        )],
        "intervals": [[g, kind, a, b] for g in sorted(groups) for kind in ("outage", "possible")
                      for a, b in _ranges([(a, b) for key, k, a, b in intervals if key == g and k == kind], stamp)],
    }


def revision(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def validate_record(record):
    """Stored comparison data is untrusted and must not break live updates."""
    if record is None:
        return
    for key, size in (("coverage", 3), ("intervals", 4)):
        rows = record[key]
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError("Invalid schedule comparison data")
        for row in rows:
            if not isinstance(row, list) or len(row) != size or not isinstance(row[0], str):
                raise ValueError("Invalid schedule comparison row")
            if size == 4 and row[1] not in ("outage", "possible"):
                raise ValueError("Invalid schedule interval kind")
            start, end = row[-2:]
            if type(start) not in (int, float) or type(end) not in (int, float) or not start < end:
                raise ValueError("Invalid schedule comparison time")
            datetime.fromtimestamp(start, timezone.utc)
            datetime.fromtimestamp(end, timezone.utc)
