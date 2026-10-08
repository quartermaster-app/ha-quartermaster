"""Diagnostics for Quartermaster."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .api import ItemStatus
from .attribution import async_wrapped_intents
from .const import DOMAIN
from .coordinator import ISSUES, QuartermasterConfigEntry, issue_id
from .identity import knows_server_id

# The household name and server address identify the household; the token is a secret.
TO_REDACT = {CONF_TOKEN, CONF_URL, "title", "unique_id", "household"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: QuartermasterConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry. No token, address, names, or item text."""
    coordinator = entry.runtime_data
    data = coordinator.data
    issues = ir.async_get(hass)
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "server": {
            "version": coordinator.status.version,
            "protocol": coordinator.status.protocol,
            "ha_api": coordinator.status.ha_api,
            "reports_server_id": coordinator.status.server_id is not None,
            "entry_uses_server_id": knows_server_id(entry),
            "household_named": coordinator.status.household_name is not None,
        },
        "items": {
            "total": len(data.items),
            "needs_action": sum(1 for item in data.items if item.status is ItemStatus.NEEDS_ACTION),
            "completed": sum(1 for item in data.items if item.status is ItemStatus.COMPLETED),
            "with_description": sum(1 for item in data.items if item.description),
        },
        "presence": {"members": len(data.presence), "shopping": data.shoppers},
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "polling_interval": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
            "unreachable_since": coordinator.unreachable_since,
        },
        "stream": {
            "connected": coordinator.stream_connected,
            "connected_at": coordinator.stream_connected_at,
            "last_event_at": coordinator.last_event_at,
            "last_error": _redact_host(coordinator.last_stream_error, coordinator.client.url),
        },
        "attribution": {"wrapped_intents": async_wrapped_intents(hass)},
        "repair_issues": [issue for issue in ISSUES if issues.async_get_issue(DOMAIN, issue_id(issue, entry))],
    }


def _redact_host(text: str | None, url: str) -> str | None:
    """Drop the server address from an error message (connection errors name the host)."""
    host = urlparse(url).hostname
    if text is None or not host:
        return text
    return text.replace(host, "**REDACTED**")
