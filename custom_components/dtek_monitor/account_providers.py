"""Cabinet contracts and rollout, independent of public outage support."""

from dataclasses import dataclass
from types import MappingProxyType

from .account import AccountError
from .providers import DEFAULT_PROVIDER_ID, PROVIDERS

# Public application ID in each operator's /main-page/488.js (2026-10-19).
_OAUTH_CLIENT_ID = "ee2b502c-e74e-476c-bb1a-dc7e075e2c47"


@dataclass(frozen=True)
class AccountProviderSpec:
    """Trusted, code-defined settings; one spec per authenticated session."""

    provider_id: str
    cabinet_origin: str
    auth_origin: str
    household_prefixes: tuple[str, ...]
    household_length: int = 12
    oauth_client_id: str = _OAUTH_CLIENT_ID
    enabled: bool = False
    experimental: bool = False
    # Explicit old-cabinet routes from each operator's official frontend.
    # Experimental regions use the same identity checks before accepting data.
    group_origin: str | None = None
    group_site: str | None = None
    legacy_household_lengths: tuple[int, ...] = ()

    @property
    def callback(self) -> str:
        return self.cabinet_origin + "/oauth/callback"


# Authenticated household reads are verified for Odesa and Kyiv city.
# Kyiv oblast and Dnipropetrovsk oblast are opt-in through normal setup, with
# an experimental notice until authenticated household data is verified live.
# Old-number lookup is enabled independently from the group metadata handoff.
ACCOUNT_PROVIDER_SPECS = MappingProxyType(
    {
        "dtek_oem": AccountProviderSpec(
            "dtek_oem",
            "https://my.dtek-oem.com.ua",
            "https://auth.my.dtek-oem.com.ua",
            ("572",),
            enabled=True,
            group_origin="https://ok.dtek-oem.com.ua",
            group_site="oem",
        ),
        "dtek_kem": AccountProviderSpec(
            "dtek_kem",
            "https://my.dtek-kem.com.ua",
            "https://auth.my.dtek-kem.com.ua",
            ("452",),
            enabled=True,
            group_origin="https://biz.ok.dtek-kem.com.ua",
            group_site="kem",
            legacy_household_lengths=(11,),
        ),
        "dtek_krem": AccountProviderSpec(
            "dtek_krem",
            "https://my.dtek-krem.com.ua",
            "https://auth.my.dtek-krem.com.ua",
            ("842",),
            enabled=True,
            experimental=True,
            group_origin="https://ok.dtek-krem.com.ua",
            group_site="krem",
        ),
        "dtek_dnem": AccountProviderSpec(
            "dtek_dnem",
            "https://my.dtek-dnem.com.ua",
            "https://auth.my.dtek-dnem.com.ua",
            ("12",),
            enabled=True,
            experimental=True,
            group_origin="https://ok.dtek-dnem.com.ua",
            group_site="dnem",
        ),
    }
)


def account_provider_supported(provider_id: str) -> bool:
    spec = ACCOUNT_PROVIDER_SPECS.get(provider_id)
    return spec is not None and spec.enabled


def account_cabinet_url(provider_id: str) -> str | None:
    """Local navigation metadata; never fall back to another operator."""
    spec = ACCOUNT_PROVIDER_SPECS.get(provider_id)
    return spec.cabinet_origin + "/" if spec is not None else None


def account_connection_title(provider_id: str, phone: str) -> str:
    """Distinguish cabinets on the same phone without exposing credentials."""
    return f"{PROVIDERS[provider_id].name} • ••••{phone[-4:]}"


def get_account_provider_spec(
    provider_id: str = DEFAULT_PROVIDER_ID,
) -> AccountProviderSpec:
    """Gate setup, SMS and runtime clients on the same explicit rollout list."""
    if not account_provider_supported(provider_id):
        raise AccountError("Personal accounts are not supported for this operator")
    return ACCOUNT_PROVIDER_SPECS[provider_id]
