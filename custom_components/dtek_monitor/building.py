"""Conservative cabinet-to-public-directory matching, without apartment data."""

import re

from .const import CONF_CITY, CONF_HOUSE, CONF_STREET
from .provider import Address

# Expand only explicit address types. Keep village/settlement/district and street
# types distinct; identical names alone are not proof of the same building.
_LOCALITY_TYPES = (
    (r"с\.\s*", "село "),
    (r"с-ще\s+", "селище "),
    (r"смт\.?\s+", "селище міського типу "),
    (r"хут\.\s*", "хутір "),
)
_STREET_TYPES = (
    (r"вул\.\s*", "вулиця "),
    (r"дор\.\s*", "дорога "),
    (r"пров\.\s*", "провулок "),
    (r"(?:просп\.\s*|пр-т\s+)", "проспект "),
    (r"(?:бул\.\s*|б-р\s+)", "бульвар "),
    (r"пл\.\s*", "площа "),
    (r"узв\.\s*", "узвіз "),
)


def building_suggestion(address: str) -> dict[str, str]:
    match = re.fullmatch(
        r"\s*(?P<city>[^,]+),\s*(?P<street>[^,]+?)(?:,\s*|\s+)"
        r"(?:буд\.\s*|будинок\s+)"
        r"(?P<house_num>[\w/\-]+)(?:,\s*(?:кв\.?|к\.|квартира)\s*[\w/\-]+)?\s*",
        address,
    )
    return {k: v.strip() for k, v in match.groupdict().items()} if match else {}


def normalized(value: str, key: str) -> str:
    text = " ".join(value.casefold().split())
    if key == CONF_CITY:
        text = re.sub(r"^(?:м\.\s*|місто\s+)", "", text)
    types = (
        _LOCALITY_TYPES
        if key == CONF_CITY
        else _STREET_TYPES
        if key == CONF_STREET
        else ()
    )
    for pattern, replacement in types:
        if re.match("^" + pattern, text):
            text = re.sub("^" + pattern, replacement, text, count=1)
            break
    return text


def unique_choice(value, choices, key):
    if not isinstance(value, str) or not value.strip():
        return None
    matches = [c for c in choices if normalized(c, key) == normalized(value, key)]
    return matches[0] if len(matches) == 1 else None


async def resolve_building(provider, suggestion):
    """Return a canonical address only if every directory match is unique."""
    if not all(suggestion.get(k) for k in (CONF_CITY, CONF_STREET, CONF_HOUSE)):
        return None
    city = unique_choice(
        suggestion[CONF_CITY], await provider.list_localities(), CONF_CITY
    )
    if city is None:
        return None
    street = unique_choice(
        suggestion[CONF_STREET], await provider.list_streets(city), CONF_STREET
    )
    if street is None:
        return None
    house = unique_choice(
        suggestion[CONF_HOUSE], await provider.list_houses(city, street), CONF_HOUSE
    )
    return Address(provider.provider_id, city, street, house) if house else None


async def verify_building(provider, values):
    """Validate an explicit directory selection without choosing among aliases."""
    city, street, house = (values.get(k) for k in (CONF_CITY, CONF_STREET, CONF_HOUSE))
    if not all(isinstance(v, str) and v for v in (city, street, house)):
        return None
    if city not in await provider.list_localities():
        return None
    if street not in await provider.list_streets(city):
        return None
    if house not in await provider.list_houses(city, street):
        return None
    return Address(provider.provider_id, city, street, house)


def address_unique_id(address):
    from .providers import DEFAULT_PROVIDER_ID

    return (
        "_".join(address.parts)
        if address.provider_id == DEFAULT_PROVIDER_ID
        else address.identity
    )
