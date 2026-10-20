"""Supported tariff schemes; retain DTEK's wire codes in records and submissions."""

SUBMISSION_SCHEMES = (
    frozenset({"GENERAL"}),
    frozenset({"DAY", "NIGHT"}),
    frozenset({"DAY_2_ZONE", "NIGHT_2_ZONE"}),
)
SUBMISSION_ZONES = tuple(sorted(frozenset().union(*SUBMISSION_SCHEMES)))


def display_zone(zone):
    """Only presentation treats the two known Day/Night spellings as aliases."""
    return {"DAY_2_ZONE": "DAY", "NIGHT_2_ZONE": "NIGHT"}.get(zone, zone)
