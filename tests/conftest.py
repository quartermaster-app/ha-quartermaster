"""Fixtures for Quartermaster tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.quartermaster.api import StreamEvent
from custom_components.quartermaster.const import DOMAIN

URL = "http://qm.test"
TOKEN = "qm_testtoken"
HOUSEHOLD = "Maple Street"
ENTITY = "todo.maple_street"

SERVER_ID = "0192a3b4-0000-7000-8000-000000000001"
STATUS = {
    "server_id": SERVER_ID,
    "version": "0.1.0",
    "protocol": 1,
    "ha_api": 1,
    "setup_required": False,
    "household": HOUSEHOLD,
}
ITEMS = [
    {"uid": "req-1", "summary": "Eggs", "description": "2 dozen", "status": "needs_action"},
    {"uid": "req-2", "summary": "Bread", "description": None, "status": "completed"},
]
PRESENCE = [
    {"user_id": "u1", "last_sync_at": "2026-10-07T17:00:00Z", "trip": None},
    {"user_id": "u2", "last_sync_at": "2026-10-07T17:00:00Z", "trip": {"id": "t1", "store_id": "s1"}},
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in every test."""


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOUSEHOLD,
        unique_id=SERVER_ID,
        version=1,
        minor_version=2,
        data={"url": URL, "token": TOKEN, "verify_ssl": True},
    )


class FakeStream:
    """Stands in for /api/events: tests push events (or errors) onto a queue."""

    def __init__(self) -> None:
        """Initialize the fake."""
        self.queue: asyncio.Queue[StreamEvent | BaseException] = asyncio.Queue()
        self.connections = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        """Behave like QuartermasterClient.async_stream_events."""
        self.connections += 1
        while True:
            event = await self.queue.get()
            if isinstance(event, BaseException):
                raise event
            yield event

    def push(self, event: str, data: dict[str, Any]) -> None:
        """Send an event."""
        self.queue.put_nowait(StreamEvent(event, data))

    def fail(self, err: BaseException) -> None:
        """Drop the connection with an error."""
        self.queue.put_nowait(err)


@pytest.fixture
def fake_stream() -> Generator[FakeStream]:
    """Replace the event stream with a controllable fake."""
    stream = FakeStream()
    with patch(
        "custom_components.quartermaster.api.QuartermasterClient.async_stream_events",
        side_effect=stream,
    ):
        yield stream


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Default answers from a healthy server."""
    aioclient_mock.get(f"{URL}/api/status", json=STATUS)
    aioclient_mock.get(f"{URL}/api/ha/items", json=ITEMS)
    aioclient_mock.get(f"{URL}/api/presence", json=PRESENCE)
    return aioclient_mock


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def calls(aioclient_mock: AiohttpClientMocker, method: str, path: str | None = None) -> list[tuple[str, Any]]:
    """Requests made with this method (and path), as (url, body)."""
    return [
        (str(url), data)
        for m, url, data, _ in aioclient_mock.mock_calls
        if m.upper() == method and (path is None or str(url) == f"{URL}{path}")
    ]
