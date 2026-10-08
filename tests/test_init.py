"""Setup, unload and repair issues tied to the entry lifecycle."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.quartermaster.const import DOMAIN

from .conftest import HOUSEHOLD, ITEMS, STATUS, URL, FakeStream, setup_entry


async def test_setup_and_unload(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """One service device per server; unloading cleans up."""
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, config_entry.entry_id), config_entry.entry_id)
    assert device is not None
    assert (device.name, device.manufacturer, device.sw_version) == (HOUSEHOLD, "Quartermaster", "0.1.0")
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.configuration_url == URL
    assert fake_stream.connections == 1

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("status", "items"),
    [
        ({"exc": aiohttp.ClientConnectionError()}, {"json": ITEMS}),
        ({"text": "<html>"}, {"json": ITEMS}),
        ({"json": STATUS}, {"exc": TimeoutError()}),
        ({"json": STATUS}, {"status": 500, "json": {"error": "boom"}}),
    ],
)
async def test_setup_not_ready(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    fake_stream: FakeStream,
    status: dict[str, object],
    items: dict[str, object],
) -> None:
    """An unreachable or odd server means retry later."""
    aioclient_mock.get(f"{URL}/api/status", **status)
    aioclient_mock.get(f"{URL}/api/ha/items", **items)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert fake_stream.connections == 0


async def test_setup_auth_failed(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """A rejected token on the first fetch starts reauthentication."""
    aioclient_mock.get(f"{URL}/api/status", json=STATUS)
    aioclient_mock.get(f"{URL}/api/ha/items", status=401, json={"error": "Not signed in"})
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_entry_without_verify_ssl(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """Entries created by 0.1 have no verify_ssl and keep verifying."""
    entry = MockConfigEntry(domain=DOMAIN, title=HOUSEHOLD, unique_id=URL, data={"url": URL, "token": "qm_x"})
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED


async def test_old_server_raises_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """A server older than supported raises a repair issue, removed on unload."""
    aioclient_mock.get(f"{URL}/api/status", json={k: v for k, v in STATUS.items() if k != "ha_api"})
    aioclient_mock.get(f"{URL}/api/ha/items", json=ITEMS)
    aioclient_mock.get(f"{URL}/api/presence", json=[])
    await setup_entry(hass, config_entry)
    issue_id = f"unsupported_server_version_{config_entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {
        "title": HOUSEHOLD,
        "version": "0.1.0",
        "ha_api": "None",
        "supported_ha_api": "1",
    }

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


# The entry stays loaded after a failed unload, so its timers are still running.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_failed_unload_keeps_issues(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """If the platforms can't unload, the entry's repair issues stay."""
    aioclient_mock.get(f"{URL}/api/status", json={**STATUS, "ha_api": 0})
    aioclient_mock.get(f"{URL}/api/ha/items", json=ITEMS)
    aioclient_mock.get(f"{URL}/api/presence", json=[])
    await setup_entry(hass, config_entry)
    with patch.object(hass.config_entries, "async_unload_platforms", return_value=False):
        assert not await hass.config_entries.async_unload(config_entry.entry_id)
    issue_id = f"unsupported_server_version_{config_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
