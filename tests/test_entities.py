"""Sensor and binary sensor entities."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.quartermaster.api import QuartermasterConnectionError

from .conftest import URL, FakeStream, setup_entry

SHOPPERS = "sensor.maple_street_shoppers"
LIVE = "binary_sensor.maple_street_live_updates"


async def test_entities_registered(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    entity_registry: er.EntityRegistry,
) -> None:
    """Translated names, stable unique IDs, and live updates disabled by default as a diagnostic."""
    await setup_entry(hass, config_entry)
    shoppers = entity_registry.async_get(SHOPPERS)
    live = entity_registry.async_get(LIVE)
    assert shoppers is not None
    assert live is not None
    assert (shoppers.unique_id, shoppers.translation_key) == (f"{config_entry.entry_id}_shoppers", "shoppers")
    assert shoppers.disabled_by is None
    assert (live.unique_id, live.translation_key) == (f"{config_entry.entry_id}_live_updates", "live_updates")
    assert live.entity_category is EntityCategory.DIAGNOSTIC
    assert live.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    state = hass.states.get(SHOPPERS)
    assert state is not None
    assert state.state == "1"
    assert state.attributes["friendly_name"] == "Maple Street Shoppers"
    assert state.attributes["unit_of_measurement"] == "shoppers"


async def test_live_updates_sensor(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    entity_registry: er.EntityRegistry,
) -> None:
    """On while the stream is connected; off (never unavailable) otherwise."""
    await setup_entry(hass, config_entry)
    entity_registry.async_update_entity(LIVE, disabled_by=None)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LIVE).state == STATE_OFF  # type: ignore[union-attr]

    fake_stream.push("changed", {"seq": 1})
    await hass.async_block_till_done()
    assert hass.states.get(LIVE).state == STATE_ON  # type: ignore[union-attr]

    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/ha/items", exc=TimeoutError())
    fake_stream.fail(QuartermasterConnectionError("gone"))
    await hass.async_block_till_done()
    # The refresh waits out the debouncer's cooldown after the last one.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert hass.states.get(LIVE).state == STATE_OFF  # type: ignore[union-attr]
    assert hass.states.get(SHOPPERS).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
