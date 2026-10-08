"""Server identity: migration to server IDs and adopting them later."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.quartermaster import async_migrate_entry
from custom_components.quartermaster.const import DOMAIN

from .conftest import HOUSEHOLD, ITEMS, PRESENCE, SERVER_ID, STATUS, TOKEN, URL, FakeStream, setup_entry

OLD_STATUS = {k: v for k, v in STATUS.items() if k not in ("server_id", "ha_api")}


def v1_entry(url: str = URL, minor_version: int = 1) -> MockConfigEntry:
    """An entry as 0.2.0 created it: version 1.1, unique ID is the address."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOUSEHOLD,
        unique_id=url,
        version=1,
        minor_version=minor_version,
        data={"url": url, "token": TOKEN, "verify_ssl": True},
    )


def serve(aioclient_mock: AiohttpClientMocker, status: dict[str, object] | None = None, url: str = URL) -> None:
    """Answer like a server at `url`."""
    aioclient_mock.get(f"{url}/api/status", json=status or STATUS)
    aioclient_mock.get(f"{url}/api/ha/items", json=ITEMS)
    aioclient_mock.get(f"{url}/api/presence", json=PRESENCE)


async def test_migration_adopts_server_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """1.1 entries switch to the server ID during migration."""
    serve(aioclient_mock)
    entry = v1_entry()
    await setup_entry(hass, entry)
    assert (entry.version, entry.minor_version, entry.unique_id) == (1, 2, SERVER_ID)


async def test_migration_offline_then_setup_adopts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """If the server is down during migration, the entry keeps its address until it answers."""
    aioclient_mock.get(f"{URL}/api/status", exc=TimeoutError())
    entry = v1_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert (entry.minor_version, entry.unique_id) == (2, URL)

    aioclient_mock.clear_requests()
    serve(aioclient_mock)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == SERVER_ID


async def test_old_server_keeps_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """A server without a server ID leaves the address as the unique ID."""
    serve(aioclient_mock, OLD_STATUS)
    entry = v1_entry()
    await setup_entry(hass, entry)
    assert (entry.minor_version, entry.unique_id) == (2, URL)


async def test_migration_from_future_version(hass: HomeAssistant) -> None:
    """Entries from a newer integration version aren't touched."""
    entry = MockConfigEntry(domain=DOMAIN, version=2, data={"url": URL, "token": TOKEN})
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)
    current = v1_entry(minor_version=2)
    current.add_to_hass(hass)
    assert await async_migrate_entry(hass, current)
    assert current.unique_id == URL


async def test_same_server_twice_keeps_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, fake_stream: FakeStream, config_entry: MockConfigEntry
) -> None:
    """An address entry for a server another entry already has keeps its address (and a warning)."""
    other_url = "http://lan.test"
    serve(aioclient_mock)
    serve(aioclient_mock, url=other_url)
    await setup_entry(hass, config_entry)
    entry = v1_entry(other_url)
    await setup_entry(hass, entry)
    assert entry.unique_id == other_url


async def test_different_server_at_address(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    fake_stream: FakeStream,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If a different server answers at the address, the entry keeps its ID and logs why."""
    serve(aioclient_mock, {**STATUS, "server_id": "replaced"})
    await setup_entry(hass, config_entry)
    assert config_entry.unique_id == SERVER_ID
    assert "now answers as a different Quartermaster server" in caplog.text
