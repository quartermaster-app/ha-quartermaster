"""Which Quartermaster server a config entry belongs to.

Servers report a stable `server_id` in /api/status. Entries use it as their
unique ID. Entries created before servers had one (or for servers that
still don't) use the normalized address instead, and switch to the server
ID as soon as the server reports one.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .api import ServerStatus, normalize_url
from .const import DOMAIN, LOGGER


def url_unique_id(url: str) -> str:
    """Return the fallback unique ID for a server without a server ID."""
    return normalize_url(url).lower()


def unique_id_for(status: ServerStatus, url: str) -> str:
    """Return the unique ID for a server: its server ID, else its address."""
    return status.server_id or url_unique_id(url)


def knows_server_id(entry: ConfigEntry) -> bool:
    """Return True when the entry's unique ID is a server ID (not an address)."""
    return entry.unique_id is not None and not entry.unique_id.startswith(("http://", "https://"))


def is_other_server(entry: ConfigEntry, status: ServerStatus) -> bool:
    """Return True when the entry is known to belong to a different server than `status`."""
    return knows_server_id(entry) and status.server_id is not None and status.server_id != entry.unique_id


@callback
def async_adopt_server_id(hass: HomeAssistant, entry: ConfigEntry, status: ServerStatus) -> None:
    """Switch an address-based entry to the server ID once the server reports one."""
    if status.server_id is None or entry.unique_id == status.server_id:
        return
    if knows_server_id(entry):
        LOGGER.warning(
            "%s now answers as a different Quartermaster server; if it was replaced, set the entry up again",
            entry.title,
        )
        return
    if any(
        other.unique_id == status.server_id
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ):
        LOGGER.warning("%s is the same Quartermaster server as another entry; remove one of them", entry.title)
        return
    LOGGER.debug("Identifying %s by its server ID from now on", entry.title)
    hass.config_entries.async_update_entry(entry, unique_id=status.server_id)
