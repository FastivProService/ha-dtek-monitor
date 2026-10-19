"""Known operators, with explicit rollout gates independent of transport support."""

from dataclasses import dataclass
from types import MappingProxyType

from .provider import ProviderError

DEFAULT_PROVIDER_ID = "dtek_oem"
CONF_PROVIDER = "provider_id"


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    base_url: str
    fixed_city: str | None = None
    enabled: bool = False


PROVIDERS = MappingProxyType(
    {
        "dtek_oem": ProviderSpec(
            "dtek_oem",
            "ДТЕК Одеські електромережі",
            "https://www.dtek-oem.com.ua",
            enabled=True,
        ),
        "dtek_krem": ProviderSpec(
            "dtek_krem",
            "ДТЕК Київські регіональні електромережі",
            "https://www.dtek-krem.com.ua",
            enabled=True,
        ),
        "dtek_dnem": ProviderSpec(
            "dtek_dnem",
            "ДТЕК Дніпровські електромережі",
            "https://www.dtek-dnem.com.ua",
            enabled=True,
        ),
        "dtek_kem": ProviderSpec(
            "dtek_kem",
            "ДТЕК Київські електромережі",
            "https://www.dtek-kem.com.ua",
            fixed_city="м. Київ",
            enabled=True,
        ),
    }
)


def create_provider(provider_id: str = DEFAULT_PROVIDER_ID, *, hass=None):
    """Only validated rollout targets can be instantiated by HA/config flow."""
    from .dtek_provider import DTEKWebProvider
    from .public_requests import operator_gate

    spec = PROVIDERS.get(provider_id)
    if spec is None or not spec.enabled:
        raise ProviderError(f"Provider is not enabled: {provider_id}")
    gate = operator_gate(hass, provider_id) if hass is not None else None
    return DTEKWebProvider(spec, request_gate=gate)
