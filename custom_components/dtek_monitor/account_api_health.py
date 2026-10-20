"""Actionable HA repairs for API removal, never inferred from network outages."""

from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUES_URL = "https://github.com/groove-max/ha-dtek-monitor/issues"
RELEASES_URL = "https://github.com/groove-max/ha-dtek-monitor/releases/latest"
INTERFACES = {
    "account_groups": "Optional legacy cabinet: account outage groups",
    "cabinet": "/api/billing/joint",
    "meter_details": "/api/billing-service/api/…/data",
    "meter_readings": "/api/billing-service/api/…/meters-with-readings",
    "meter_history": "/api/billing-service/api/…/history",
    "meter_consumption": "/api/billing-service/api/…/customer-book",
    "meter_submission": "/api/billing-service/api/…/readings",
    "login": "DTEK OAuth",
    "link_lookup": "/api/billing/joint/info/get_accounts",
    "link_add": "/api/billing/joint/objects/add",
    "link_remove": "/api/billing/joint/objects/remove",
}


def report_api_issue(hass, provider, error, *, scope=None):
    interface = error.interface
    if interface not in INTERFACES:
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"account_api_{provider}_{interface}" + (f"_{scope}" if scope else ""),
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING if interface == "account_groups" else ir.IssueSeverity.ERROR,
        translation_key="account_groups_api_changed" if interface == "account_groups" else "account_api_changed",
        translation_placeholders={
            "interface": INTERFACES[interface],
            "reason": error.reason,
            "issues_url": ISSUES_URL,
            "releases_url": RELEASES_URL,
        },
        learn_more_url=ISSUES_URL,
    )


def clear_api_issue(hass, provider, interface, *, scope=None):
    ir.async_delete_issue(hass, DOMAIN, f"account_api_{provider}_{interface}" + (f"_{scope}" if scope else ""))


def watch_api(hass, provider, client):
    """Observe interactive requests as well as the scheduled coordinator refresh."""
    client.api_issue_handler = lambda error: report_api_issue(hass, provider, error)
    client.api_recovered_handler = lambda interface: clear_api_issue(
        hass, provider, interface
    )
