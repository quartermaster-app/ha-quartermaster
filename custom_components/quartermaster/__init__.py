"""The Quartermaster integration: the household shopping list as a to-do list."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import QuartermasterClient, QuartermasterError
from .api.client import DEFAULT_REQUEST_TIMEOUT
from .attribution import async_setup_intent_hooks
from .const import DOMAIN, LOGGER
from .coordinator import ISSUES, QuartermasterConfigEntry, QuartermasterCoordinator, issue_id
from .identity import async_adopt_server_id

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.TODO]

# Migration runs before setup and blocks it, so don't wait long for the server.
MIGRATION_TIMEOUT = 5


def _client(
    hass: HomeAssistant, entry: ConfigEntry, request_timeout: float = DEFAULT_REQUEST_TIMEOUT
) -> QuartermasterClient:
    return QuartermasterClient(
        async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
        request_timeout=request_timeout,
    )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entries.

    1.1 → 1.2: identify the server by its server ID instead of its address.
    If the server can't be reached now (or doesn't report an ID yet), the
    entry keeps its address; setup switches it over once the server reports one.
    """
    if entry.version > 1:
        return False  # from a newer version of the integration
    if entry.minor_version < 2:
        try:
            async_adopt_server_id(hass, entry, await _client(hass, entry, MIGRATION_TIMEOUT).async_get_status())
        except QuartermasterError as err:
            LOGGER.debug("Couldn't read the server ID while migrating %s: %s", entry.title, err)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: QuartermasterConfigEntry) -> bool:
    """Set up Quartermaster from a config entry."""
    client = _client(hass, entry)
    try:
        status = await client.async_get_status()
    except QuartermasterError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err
    async_adopt_server_id(hass, entry, status)

    coordinator = QuartermasterCoordinator(hass, entry, client, status)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_stream()
    async_setup_intent_hooks(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: QuartermasterConfigEntry) -> bool:
    """Unload a config entry; its repair issues go with it."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for issue in ISSUES:
            ir.async_delete_issue(hass, DOMAIN, issue_id(issue, entry))
    return unloaded
