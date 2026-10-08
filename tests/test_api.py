"""Tests for the bundled API client (no Home Assistant behaviour involved)."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.quartermaster.api import (
    Item,
    ItemStatus,
    Presence,
    QuartermasterAuthError,
    QuartermasterClient,
    QuartermasterConnectionError,
    QuartermasterInvalidResponseError,
    QuartermasterNotFoundError,
    QuartermasterRequestError,
    ServerStatus,
    SSEParser,
    Via,
    parse_version,
)

from .conftest import ITEMS, PRESENCE, SERVER_ID, STATUS, TOKEN, URL


@pytest.fixture
async def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> QuartermasterClient:
    """A client on Home Assistant's (mocked) session, with a messy URL."""
    return QuartermasterClient(async_get_clientsession(hass), f" {URL}// ", TOKEN)


async def test_status_needs_no_token(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Status is read without the token and parsed."""
    aioclient_mock.get(f"{URL}/api/status", json=STATUS)
    status = await client.async_get_status()
    assert status == ServerStatus(
        version="0.1.0", protocol=1, setup_required=False, household="Maple Street", server_id=SERVER_ID, ha_api=1
    )
    assert status.household_name == "Maple Street"
    assert "Authorization" not in aioclient_mock.mock_calls[0][3]


@pytest.mark.parametrize(
    ("data", "household_name", "protocol"),
    [
        ({"version": "0.1.0", "household": "Household"}, None, None),
        ({"version": "0.1.0", "household": "  "}, None, None),
        ({"version": "0.1.0", "protocol": "1"}, None, None),
        ({"version": "0.1.0", "protocol": 2, "household": " Home "}, "Home", 2),
        ({"version": "0.1.0", "protocol": True}, None, None),
    ],
)
def test_status_defaults(data: dict[str, object], household_name: str | None, protocol: int | None) -> None:
    """An unnamed household has no name; odd fields are tolerated."""
    status = ServerStatus.from_dict(data)
    assert status.household_name == household_name
    assert status.protocol == protocol


@pytest.mark.parametrize(
    ("data", "server_id", "ha_api"),
    [
        ({"version": "0.1.0"}, None, None),
        ({"version": "0.1.0", "server_id": "  ", "ha_api": "1"}, None, None),
        ({"version": "0.1.0", "server_id": 5, "ha_api": False}, None, None),
        ({"version": "0.1.0", "server_id": " abc ", "ha_api": 2}, "abc", 2),
    ],
)
def test_status_identity(data: dict[str, object], server_id: str | None, ha_api: int | None) -> None:
    """Servers before 0.1.x lacked server_id and ha_api; odd values are ignored."""
    status = ServerStatus.from_dict(data)
    assert (status.server_id, status.ha_api) == (server_id, ha_api)


@pytest.mark.parametrize("data", [[], {"household": "x"}, {"version": 1}])
def test_status_not_quartermaster(data: object) -> None:
    """Anything without a version string isn't Quartermaster."""
    with pytest.raises(QuartermasterInvalidResponseError):
        ServerStatus.from_dict(data)


async def test_get_items(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Items are parsed and the token is sent."""
    aioclient_mock.get(f"{URL}/api/ha/items", json=[*ITEMS, {"uid": "req-3", "status": "weird"}])
    items = await client.async_get_items()
    assert items == [
        Item("req-1", "Eggs", "2 dozen", ItemStatus.NEEDS_ACTION),
        Item("req-2", "Bread", None, ItemStatus.COMPLETED),
        Item("req-3", "", None, ItemStatus.NEEDS_ACTION),
    ]
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("data", [{"items": []}, [{"summary": "no uid"}], ["text"]])
async def test_get_items_invalid(
    client: QuartermasterClient, aioclient_mock: AiohttpClientMocker, data: object
) -> None:
    """A malformed list is an invalid response."""
    aioclient_mock.get(f"{URL}/api/ha/items", json=data)
    with pytest.raises(QuartermasterInvalidResponseError):
        await client.async_get_items()


async def test_not_json(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """A web page instead of JSON means it isn't a Quartermaster server."""
    aioclient_mock.get(f"{URL}/api/status", text="<html>")
    with pytest.raises(QuartermasterInvalidResponseError, match="did not return JSON"):
        await client.async_get_status()


@pytest.mark.parametrize(
    ("status", "body", "error", "message"),
    [
        (401, {"error": "Not signed in"}, QuartermasterAuthError, "Not signed in"),
        (403, {"error": "This token cannot do that"}, QuartermasterAuthError, "cannot do that"),
        (404, {"error": "No such item"}, QuartermasterNotFoundError, "No such item"),
        (400, {"error": "summary required"}, QuartermasterRequestError, "summary required"),
        (502, None, QuartermasterRequestError, "HTTP 502"),
        (500, ["odd"], QuartermasterRequestError, "HTTP 500"),
    ],
)
async def test_http_errors(
    client: QuartermasterClient,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: object,
    error: type[Exception],
    message: str,
) -> None:
    """HTTP errors map to client errors carrying the server's message."""
    if body is None:
        aioclient_mock.get(f"{URL}/api/ha/items", status=status, text="Bad gateway")
    else:
        aioclient_mock.get(f"{URL}/api/ha/items", status=status, json=body)
    with pytest.raises(error, match=message):
        await client.async_get_items()


async def test_request_error_keeps_status(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """QuartermasterRequestError carries the HTTP status."""
    aioclient_mock.delete(f"{URL}/api/ha/items/x", status=409, json={"error": "Rejected"})
    with pytest.raises(QuartermasterRequestError) as err:
        await client.async_delete_item("x")
    assert (err.value.status, err.value.message) == (409, "Rejected")


@pytest.mark.parametrize("exc", [aiohttp.ClientConnectionError("refused"), TimeoutError()])
async def test_connection_errors(
    client: QuartermasterClient, aioclient_mock: AiohttpClientMocker, exc: Exception
) -> None:
    """Network failures and timeouts are connection errors."""
    aioclient_mock.get(f"{URL}/api/ha/items", exc=exc)
    with pytest.raises(QuartermasterConnectionError):
        await client.async_get_items()


async def test_presence(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Presence lists who is on a trip."""
    aioclient_mock.get(f"{URL}/api/presence", json=PRESENCE)
    presence = await client.async_get_presence()
    assert presence == [
        Presence("u1", "2026-10-07T17:00:00Z", None, None),
        Presence("u2", "2026-10-07T17:00:00Z", "t1", "s1"),
    ]
    assert [p.shopping for p in presence] == [False, True]


@pytest.mark.parametrize("data", [{"users": []}, ["text"]])
def test_presence_invalid(data: object) -> None:
    """Malformed presence is an invalid response."""
    with pytest.raises(QuartermasterInvalidResponseError):
        Presence.list_from(data)


async def test_add_item(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Adding sends the text and attribution and returns what was created."""
    aioclient_mock.post(f"{URL}/api/ha/items", json={"items": ITEMS[:1], "possible_duplicate": True})
    result = await client.async_add_item("2 dozen eggs", None, Via("satellite", "Kitchen"))
    assert result.possible_duplicate
    assert [item.uid for item in result.items] == ["req-1"]
    assert aioclient_mock.mock_calls[0][2] == {
        "summary": "2 dozen eggs",
        "description": None,
        "via": {"kind": "satellite", "name": "Kitchen"},
    }


async def test_add_item_unattributed(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """No via means no via key; odd answers are rejected."""
    aioclient_mock.post(f"{URL}/api/ha/items", json={})
    result = await client.async_add_item("milk", "from the corner shop")
    assert result.items == []
    assert not result.possible_duplicate
    assert aioclient_mock.mock_calls[0][2] == {"summary": "milk", "description": "from the corner shop"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{URL}/api/ha/items", json=[])
    with pytest.raises(QuartermasterInvalidResponseError):
        await client.async_add_item("milk")


async def test_update_and_delete(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Updates send only what changed; IDs are escaped in the path."""
    aioclient_mock.patch(f"{URL}/api/ha/items/a%2Fb", json=ITEMS[1])
    aioclient_mock.delete(f"{URL}/api/ha/items/a%2Fb", json={"ok": True})
    item = await client.async_update_item(
        "a/b", summary="Rye bread", status=ItemStatus.COMPLETED, via=Via("user", "Alex")
    )
    assert item.status is ItemStatus.COMPLETED
    await client.async_update_item("a/b")
    await client.async_delete_item("a/b")
    assert [call[2] for call in aioclient_mock.mock_calls] == [
        {"summary": "Rye bread", "status": "completed", "via": {"kind": "user", "name": "Alex"}},
        {},
        None,
    ]


async def test_stream(client: QuartermasterClient, aioclient_mock: AiohttpClientMocker) -> None:
    """The stream yields events, then reports that the server closed it."""
    aioclient_mock.get(
        f"{URL}/api/events",
        text=': hello\n\nevent: changed\ndata: {"type":"changed","seq":1}\n\n: ping\n\n',
    )
    seen = []

    async def consume() -> None:
        async for event in client.async_stream_events():
            seen.append(event)  # noqa: PERF401

    with pytest.raises(QuartermasterConnectionError, match="closed by the server"):
        await consume()
    assert [(e.event, e.data) for e in seen] == [("changed", {"type": "changed", "seq": 1})]
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Accept"] == "text/event-stream"
    assert headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401, "json": {"error": "Not signed in"}}, QuartermasterAuthError),
        ({"status": 502, "text": "Bad gateway"}, QuartermasterRequestError),
        ({"exc": aiohttp.ClientConnectionError()}, QuartermasterConnectionError),
        ({"exc": TimeoutError()}, QuartermasterConnectionError),
    ],
)
async def test_stream_errors(
    client: QuartermasterClient, aioclient_mock: AiohttpClientMocker, kwargs: dict[str, object], error: type[Exception]
) -> None:
    """Opening the stream fails with the matching client error."""
    aioclient_mock.get(f"{URL}/api/events", **kwargs)
    with pytest.raises(error):
        async for _ in client.async_stream_events():
            pass  # pragma: no cover


def test_sse_parser() -> None:
    """Comments are skipped, events split on blank lines, chunks can split anywhere."""
    parser = SSEParser()
    raw = b': hello\n\nevent: changed\ndata: {"type":"changed","seq":3}\n\n: ping\n\nevent: request_added\r\ndata: {"text":"mi'
    assert [(e.event, e.data) for e in parser.feed(raw)] == [("changed", {"type": "changed", "seq": 3})]
    assert [(e.event, e.data) for e in parser.feed(b'lk"}\r\n\r\n')] == [("request_added", {"text": "milk"})]


def test_sse_parser_odd_input() -> None:
    """Multi-line data joins; non-JSON, non-object and empty events are dropped."""
    parser = SSEParser()
    events = parser.feed(
        b'data: {"a":\ndata: 1}\n\nevent: x\ndata: not json\n\ndata: [1]\n\nevent: empty\n\nid: 7\nretry: 10\n\n'
    )
    assert [(e.event, e.data) for e in events] == [("message", {"a": 1})]


def test_via() -> None:
    """Via renders for JSON and logs."""
    via = Via("satellite", "Kitchen")
    assert via.as_dict() == {"kind": "satellite", "name": "Kitchen"}
    assert str(via) == "satellite:Kitchen"


@pytest.mark.parametrize(
    ("version", "parsed"),
    [("0.2.1", (0, 2, 1)), ("v1.10-beta", (1, 10)), (" 3 ", (3,)), ("dev", ())],
)
def test_parse_version(version: str, parsed: tuple[int, ...]) -> None:
    """Versions compare numerically."""
    assert parse_version(version) == parsed
