"""The Quartermaster integration: the household shopping list as a to-do list."""

from __future__ import annotations

from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import QuartermasterClient, QuartermasterError
from .attribution import async_setup_intent_hooks
from .const import DOMAIN
from .coordinator import ISSUES, QuartermasterConfigEntry, QuartermasterCoordinator, issue_id

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.TODO]


async def async_setup_entry(hass: HomeAssistant, entry: QuartermasterConfigEntry) -> bool:
    """Set up Quartermaster from a config entry."""
    client = QuartermasterClient(
        async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
    )
    try:
        status = await client.async_get_status()
    except QuartermasterError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

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
