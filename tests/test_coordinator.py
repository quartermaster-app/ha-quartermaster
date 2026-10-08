"""Event stream, polling fallback, presence and repair issues."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.quartermaster import coordinator as coordinator_mod
from custom_components.quartermaster.api import QuartermasterAuthError, QuartermasterConnectionError
from custom_components.quartermaster.const import DOMAIN

from .conftest import ITEMS, PRESENCE, STATUS, URL, FakeStream, calls, setup_entry

REQUEST_ADDED = {
    "type": "request_added",
    "request_id": "req-9",
    "text": "milk",
    "actor": {"id": "a1", "kind": "satellite", "name": "Kitchen", "user_id": None},
    "possible_duplicate": False,
    "duplicate_of": None,
    "shoppers": [
        {"user_id": "u2", "name": "Sam", "store_id": "s1", "store": "Costco", "last_sync_at": "2026-10-07T17:00:00Z"}
    ],
}
TRIP = {"trip_id": "t1", "user_id": "u2", "user": "Sam", "store_id": "s1", "store": "Costco", "open_count": 7}
SHOPPERS = "sensor.maple_street_shoppers"


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconnect immediately in tests."""
    monkeypatch.setattr(coordinator_mod, "STREAM_BACKOFF_MIN", 0)
    monkeypatch.setattr(coordinator_mod, "STREAM_BACKOFF_MAX", 0)


def item_fetches(aioclient_mock: AiohttpClientMocker) -> int:
    """How often the list was read."""
    return len(calls(aioclient_mock, "GET", "/api/ha/items"))


def issue(hass: HomeAssistant, entry: MockConfigEntry, name: str) -> ir.IssueEntry | None:
    """The entry's repair issue, if raised."""
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{name}_{entry.entry_id}")


async def connect(hass: HomeAssistant, stream: FakeStream) -> None:
    """Deliver the `changed` event the server sends on connect."""
    stream.push("changed", {"type": "changed", "seq": 1})
    await hass.async_block_till_done()


async def test_events_refired(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """request_added, trip_started and trip_ended become quartermaster_* bus events; others don't."""
    await setup_entry(hass, config_entry)
    added = async_capture_events(hass, "quartermaster_request_added")
    started = async_capture_events(hass, "quartermaster_trip_started")
    ended = async_capture_events(hass, "quartermaster_trip_ended")

    fake_stream.push("request_added", REQUEST_ADDED)
    fake_stream.push("trip_started", {"type": "trip_started", **TRIP})
    fake_stream.push("trip_ended", {"type": "trip_ended", **TRIP, "open_count": 0})
    fake_stream.push("something_new", {"type": "something_new"})
    await hass.async_block_till_done()

    assert [e.data for e in added] == [{k: v for k, v in REQUEST_ADDED.items() if k != "type"}]
    assert [e.data for e in started] == [TRIP]
    assert ended[0].data["open_count"] == 0


async def test_changed_refreshes_and_stops_polling(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """Connecting turns polling off and turns the live updates sensor on; `changed` refetches."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=60)
    before = item_fetches(mock_api)

    await connect(hass, fake_stream)
    assert coordinator.stream_connected
    assert coordinator.update_interval is None
    assert item_fetches(mock_api) == before + 1
    # Presence comes from events while connected, so it isn't fetched again.
    assert len(calls(mock_api, "GET", "/api/presence")) == 1
    # The status is re-read after every connect.
    assert len(calls(mock_api, "GET", "/api/status")) == 2


async def test_presence_events(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """The shoppers sensor follows presence events; malformed ones are ignored."""
    await setup_entry(hass, config_entry)
    state = hass.states.get(SHOPPERS)
    assert state is not None
    assert state.state == "1"

    await connect(hass, fake_stream)
    both = [{**PRESENCE[1], "user_id": "u1"}, PRESENCE[1]]
    fake_stream.push("presence", {"type": "presence", "users": both})
    await hass.async_block_till_done()
    assert hass.states.get(SHOPPERS).state == "2"  # type: ignore[union-attr]

    fake_stream.push("presence", {"type": "presence", "users": "nope"})
    await hass.async_block_till_done()
    assert hass.states.get(SHOPPERS).state == "2"  # type: ignore[union-attr]


async def test_presence_unavailable_keeps_working(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """A server that can't report presence still loads the list."""
    aioclient_mock.get(f"{URL}/api/status", json=STATUS)
    aioclient_mock.get(f"{URL}/api/ha/items", json=ITEMS)
    aioclient_mock.get(f"{URL}/api/presence", status=404, json={"error": "Not found"})
    await setup_entry(hass, config_entry)
    assert hass.states.get(SHOPPERS).state == "0"  # type: ignore[union-attr]


async def test_stream_down_falls_back_to_polling(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
) -> None:
    """When the stream drops, poll every 60 s and keep reconnecting."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    await connect(hass, fake_stream)

    fake_stream.fail(QuartermasterConnectionError("boom"))
    await hass.async_block_till_done()
    assert not coordinator.stream_connected
    assert coordinator.update_interval == timedelta(seconds=60)
    assert coordinator.last_stream_error == "boom"

    # Still down on the next attempt: keeps the error, stays polling.
    fake_stream.fail(QuartermasterConnectionError("again"))
    await hass.async_block_till_done()
    assert coordinator.last_stream_error == "again"

    await connect(hass, fake_stream)
    assert fake_stream.connections >= 3
    assert coordinator.stream_connected
    assert coordinator.update_interval is None
    assert coordinator.last_stream_error is None


async def test_unexpected_stream_error_keeps_listening(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """A bug in event handling is logged and the listener reconnects."""
    await setup_entry(hass, config_entry)
    fake_stream.fail(ValueError("bug"))
    await hass.async_block_till_done()
    assert config_entry.runtime_data.last_stream_error == "ValueError('bug')"
    await connect(hass, fake_stream)
    assert config_entry.runtime_data.stream_connected


async def test_stream_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """A rejected token on the stream starts reauth and stops the listener."""
    await setup_entry(hass, config_entry)
    fake_stream.fail(QuartermasterAuthError("Not signed in"))
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert fake_stream.connections == 1


async def test_poll_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """A token revoked while polling starts reauth."""
    await setup_entry(hass, config_entry)
    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/ha/items", status=401, json={"error": "Not signed in"})
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_server_unreachable_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A server down for 30 minutes raises a repair issue; it clears when the server is back."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/ha/items", exc=TimeoutError())

    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert hass.states.get("todo.maple_street").state == "unavailable"  # type: ignore[union-attr]
    assert issue(hass, config_entry, "server_unreachable") is None

    freezer.tick(timedelta(minutes=31))
    await coordinator.async_refresh()
    raised = issue(hass, config_entry, "server_unreachable")
    assert raised is not None
    assert raised.translation_placeholders == {"url": URL, "title": "Maple Street"}

    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/ha/items", json=ITEMS)
    mock_api.get(f"{URL}/api/presence", json=PRESENCE)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.unreachable_since is None
    assert issue(hass, config_entry, "server_unreachable") is None


async def test_stream_unavailable_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A stream that never connects while the server answers raises a proxy repair issue."""
    await setup_entry(hass, config_entry)
    fake_stream.fail(QuartermasterConnectionError("Timeout on reading data from socket"))
    await hass.async_block_till_done()

    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    raised = issue(hass, config_entry, "stream_unavailable")
    assert raised is not None
    assert raised.translation_placeholders == {
        "title": "Maple Street",
        "error": "Timeout on reading data from socket",
    }

    await connect(hass, fake_stream)
    assert issue(hass, config_entry, "stream_unavailable") is None


async def test_no_stream_issue_when_server_down(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    freezer: FrozenDateTimeFactory,
) -> None:
    """If the whole server is down, only the unreachable issue applies."""
    await setup_entry(hass, config_entry)
    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/ha/items", exc=TimeoutError())
    await config_entry.runtime_data.async_refresh()

    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert issue(hass, config_entry, "stream_unavailable") is None


async def test_no_stream_issue_once_connected(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
) -> None:
    """A timer that fires after the stream connected does nothing."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data
    await connect(hass, fake_stream)
    coordinator._async_stream_issue_due(coordinator.stream_connected_at)  # type: ignore[arg-type]
    assert issue(hass, config_entry, "stream_unavailable") is None


async def test_reconnect_refreshes_status(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    device_registry: dr.DeviceRegistry,
) -> None:
    """After a reconnect, an upgrade or rename shows on the device; failures are ignored."""
    await setup_entry(hass, config_entry)
    coordinator = config_entry.runtime_data

    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/status", json={**STATUS, "ha_api": 2})
    mock_api.get(f"{URL}/api/ha/items", json=ITEMS)
    await connect(hass, fake_stream)
    assert issue(hass, config_entry, "incompatible_server") is not None

    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/status", json={**STATUS, "version": "0.3.0", "household": "Elm Road"})
    mock_api.get(f"{URL}/api/ha/items", json=ITEMS)
    await coordinator.async_refresh_status()
    device = device_registry.async_get_device_by_identifier((DOMAIN, config_entry.entry_id), config_entry.entry_id)
    assert device is not None
    assert (device.name, device.sw_version) == ("Elm Road", "0.3.0")
    assert issue(hass, config_entry, "incompatible_server") is None

    # Unchanged status: nothing to do.
    await coordinator.async_refresh_status()
    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/status", exc=TimeoutError())
    await coordinator.async_refresh_status()
    assert coordinator.status.version == "0.3.0"


async def test_status_refresh_without_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A status change with no device in the registry just updates the status."""
    await setup_entry(hass, config_entry)
    device = device_registry.async_get_device_by_identifier((DOMAIN, config_entry.entry_id), config_entry.entry_id)
    assert device is not None
    device_registry.async_remove_device(device.id)
    mock_api.clear_requests()
    mock_api.get(f"{URL}/api/status", json={**STATUS, "version": "0.2.0"})
    await config_entry.runtime_data.async_refresh_status()
    assert config_entry.runtime_data.status.version == "0.2.0"
