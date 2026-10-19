"""Language selection shared by DTEK's backend-generated messages."""


def is_ukrainian(language: str | None) -> bool:
    """Russian HA preferences use Ukrainian; other languages retain English."""
    return (language or "en").lower().replace("_", "-").split("-")[0] in {"uk", "ru"}
