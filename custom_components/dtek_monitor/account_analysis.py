"""Read-only comparisons of dated DTEK records; never inferred billing totals."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from .account_history import KYIV


def compare_consumption(current, previous):
    """Compare only the intersection of published closed calendar months."""
    pairs = []
    for month in range(1, 13):
        a = next(
            (
                m
                for m in current["months"]
                if m["period"] == f"{current['year']}-{month:02d}"
            ),
            {},
        )
        b = next(
            (
                m
                for m in previous["months"]
                if m["period"] == f"{previous['year']}-{month:02d}"
            ),
            {},
        )
        comparable = all(
            m.get("status") == "closed" and m.get("value") is not None for m in (a, b)
        )
        pairs.append(
            {
                "month": month,
                "current": a.get("value") if a.get("status") == "closed" else None,
                "previous": b.get("value") if b.get("status") == "closed" else None,
                "comparable": comparable,
                "difference": str(Decimal(a["value"]) - Decimal(b["value"]))
                if comparable
                else None,
            }
        )

    def aggregate(name, months):
        rows = [p for p in pairs if p["month"] in months and p["comparable"]]
        a = sum((Decimal(p["current"]) for p in rows), Decimal(0))
        b = sum((Decimal(p["previous"]) for p in rows), Decimal(0))
        return {
            "period": name,
            "months": [p["month"] for p in rows],
            "complete": len(rows) == len(months),
            "current": str(a) if rows else None,
            "previous": str(b) if rows else None,
            "difference": str(a - b) if rows else None,
        }

    return {
        "year": current["year"],
        "previous_year": previous["year"],
        "fetched_at": {
            "current": current["fetched_at"],
            "previous": previous["fetched_at"],
        },
        "months": pairs,
        "periods": [
            aggregate(name, months)
            for name, months in (
                ("year", range(1, 13)),
                ("winter", (1, 2, 12)),
                ("spring", (3, 4, 5)),
                ("summer", (6, 7, 8)),
                ("autumn", (9, 10, 11)),
            )
        ],
    }


def match_readback(attempt, history, serial=None):
    """Find matching account records, without claiming causal or settlement proof.

    Dates are reading dates, not receipt timestamps. Exact same-day matches can
    already have existed before this request. Multiple matches remain ambiguous.
    """
    result = {
        "status": "no_attempt",
        "fetched_at": history["fetched_at"],
        "attempt": None,
        "matches": [],
        "latest": history["readings"][:6],
    }
    if not attempt:
        return result
    result["attempt"] = {k: attempt.get(k) for k in ("id", "at", "status", "readings")}
    result["status"] = "not_found"
    if serial and attempt.get("meter_serial") and serial != attempt["meter_serial"]:
        result["status"] = "meter_changed"
        return result
    try:
        stamp = datetime.fromisoformat(attempt["at"])
        if stamp.tzinfo is None:
            raise ValueError
        day = stamp.astimezone(KYIV).date()
        wanted = {
            row["zone"]: Decimal(str(row["value"])) for row in attempt["readings"]
        }
        if (
            not wanted
            or len(wanted) != len(attempt["readings"])
            or any(not value.is_finite() for value in wanted.values())
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        result["status"] = "invalid_attempt"
        return result
    counts = {}
    for zone, value in wanted.items():
        rows = [
            r
            for r in history["readings"]
            if r["zone"] == zone
            and r["energy"] == "ACTIVE"
            and Decimal(r["value"]) == value
            and datetime.fromisoformat(r["date_time"]).astimezone(KYIV).date() == day
        ]
        counts[zone] = len(rows)
        result["matches"].extend(rows)
    result["status"] = (
        "ambiguous"
        if any(n > 1 for n in counts.values())
        else "matched"
        if all(counts.values())
        else "partial"
        if any(counts.values())
        else "not_found"
    )
    return result
