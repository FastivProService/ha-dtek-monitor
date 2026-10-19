"""Optional cabinet metadata. Invalid optional fields never invalidate readings."""

from copy import deepcopy
from datetime import date
from decimal import InvalidOperation, Decimal
import re


def text(value, limit=300):
    return (
        value.strip()
        if isinstance(value, str) and 0 < len(value.strip()) <= limit
        else None
    )


def year(value):
    value = str(value)
    return (
        int(value)
        if re.fullmatch(r"\d{4}", value) and 1900 <= int(value) <= 2200
        else None
    )


def day(value):
    try:
        return (
            date.fromisoformat(value[:10]).isoformat()
            if isinstance(value, str)
            else None
        )
    except ValueError:
        return None


def positive(value):
    try:
        number = Decimal(str(value).replace(",", "."))
        return (
            str(number.normalize())
            if number.is_finite() and 0 < number <= 1000000
            else None
        )
    except (InvalidOperation, ValueError):
        return None


def eic(value):
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Z0-9-]{16}", value)
        else None
    )


def parse_profile(details, linked=None):
    """Whitelist data, preserve all EICs, and identify the current installation."""
    linked = linked or {}
    d = details or {}
    codes = linked.get("eic")
    codes = (
        [code for value in codes if (code := eic(value))]
        if isinstance(codes, list)
        else []
    )
    installation = eic(d.get("installationEic"))
    if installation and installation not in codes:
        codes.append(installation)
    seals = d.get("seals")
    return {
        "metadata_available": details is not None,
        "address": text(d.get("address"), 1000) or text(linked.get("address"), 1000),
        "eics": list(dict.fromkeys(codes)),
        "installation_eic": installation,
        "serial": text(d.get("serialNum")),
        "model": text(d.get("meterType")),
        "zone_scheme": text(d.get("zoneScheme")),
        "contract_power_kw": positive(d.get("nominalPower"))
        or positive(linked.get("contractPower")),
        "manufacture_year": year(d.get("creationYear")),
        "verification_year": year(d.get("verificationYear")),
        "next_verification_year": year(d.get("nextVerificationYear")),
        "installation_date": day(d.get("installationDate")),
        "agreement_date": day(d.get("agreementDate")),
        "installation_type": text(d.get("installationObjectType")),
        "seals": [
            {
                "number": text(s.get("sealNumber")),
                "type": text(s.get("type")),
                "place": text(s.get("installationPlace")),
                "installed": day(s.get("installationDate")),
                "removed": day(s.get("deinstallationDate")),
            }
            for s in seals[:100]
            if isinstance(s, dict)
        ]
        if isinstance(seals, list)
        else None,
        # No nonempty replacement example has been verified yet. Do not invent a schema.
        "replacement_records": len(d["changeHistory"])
        if isinstance(d.get("changeHistory"), list)
        else None,
    }


def public_profile(profile, *, reveal=False):
    result = deepcopy(profile)
    if not reveal:
        mask = lambda value: "••••" + value[-4:] if value else None
        result["eics"] = [mask(value) for value in result.get("eics", [])]
        for key in ("installation_eic", "serial"):
            result[key] = mask(result.get(key))
        for seal in result.get("seals") or []:
            seal["number"] = mask(seal.get("number"))
    return result
