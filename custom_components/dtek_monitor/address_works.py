"""Repair windows from normalized address reports; never infer missing times."""

from datetime import datetime

from .provider import AddressWork

WORKS_STATUSES = ("reported", "empty", "incomplete", "unavailable", "unsupported")


def address_works(values):
    """Keep every planned work, including when an emergency is the primary one."""
    records = values.get("all_outages") or [values]
    works = set()
    incomplete = False
    for record in records:
        if record.get("outage_type") != "planned":
            continue
        start, end = record.get("outage_start"), record.get("outage_end")
        if (
            not isinstance(start, datetime)
            or not isinstance(end, datetime)
            or start.tzinfo is None
            or end.tzinfo is None
            or end <= start
        ):
            incomplete = True
            continue
        works.add(AddressWork(start, end, record.get("outage_description") or ""))
    return tuple(sorted(works, key=lambda w: (w.start, w.end, w.description))), incomplete


def works_status(coordinator):
    if not coordinator.address_success:
        return "unavailable"
    if coordinator.address_works is None:
        return "unsupported"
    if coordinator.works_incomplete:
        return "incomplete"
    return "reported" if coordinator.address_works else "empty"


def works_attributes(coordinator):
    return {
        "data_status": works_status(coordinator),
        "source": "operator_address_reports",
        "last_verified_at": (
            coordinator.address_last_success.isoformat()
            if coordinator.address_last_success else None
        ),
        "complete_interval_count": len(coordinator.address_works or ()),
        "has_incomplete_intervals": coordinator.works_incomplete,
        "advance_notice_guaranteed": False,
    }
