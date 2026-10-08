"""Async client for the Quartermaster Home Assistant API.

See docs/home-assistant-api.md in the Quartermaster repository. The caller
owns the aiohttp session; the client never creates or closes one.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import aiohttp

from .errors import (
    QuartermasterAuthError,
    QuartermasterConnectionError,
    QuartermasterError,
    QuartermasterInvalidResponseError,
    QuartermasterNotFoundError,
    QuartermasterRequestError,
)
from .models import AddResult, Item, ItemStatus, Presence, ServerStatus, StreamEvent, Via
from .sse import SSEParser

DEFAULT_REQUEST_TIMEOUT = 15
# The server sends a comment line every 25 s; silence for longer than this
# means the connection is dead even if the socket hasn't noticed.
DEFAULT_STREAM_READ_TIMEOUT = 60


def normalize_url(url: str) -> str:
    """Strip whitespace and trailing slashes from a server URL."""
    return url.strip().rstrip("/")


class QuartermasterClient:
    """Talks to one Quartermaster server with a Home Assistant token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
        *,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """Initialize the client with a session the caller owns."""
        self._session = session
        self.url = normalize_url(url)
        self._token = token
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)

    def _headers(self, *, auth: bool = True, accept: str = "application/json") -> dict[str, str]:
        headers = {"Accept": accept}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = True,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.url}{path}",
                headers=self._headers(auth=auth),
                json=body,
                timeout=self._timeout,
            ) as resp:
                await _raise_for_status(resp)
                try:
                    return await resp.json(content_type=None)
                except ValueError as err:
                    raise QuartermasterInvalidResponseError(
                        f"Not a Quartermaster server: {path} did not return JSON"
                    ) from err
        except QuartermasterError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise QuartermasterConnectionError(str(err) or type(err).__name__) from err

    async def async_get_status(self) -> ServerStatus:
        """Return the server's version and household name (no token needed)."""
        return ServerStatus.from_dict(await self._request("GET", "/api/status", auth=False))

    async def async_get_items(self) -> list[Item]:
        """Return the household list: open and partly bought items, then bought ones."""
        data = await self._request("GET", "/api/ha/items")
        if not isinstance(data, list):
            raise QuartermasterInvalidResponseError("Unexpected response for /api/ha/items")
        return [Item.from_dict(item) for item in data]

    async def async_get_presence(self) -> list[Presence]:
        """Return who is shopping right now."""
        return Presence.list_from(await self._request("GET", "/api/presence"))

    async def async_add_item(self, summary: str, description: str | None = None, via: Via | None = None) -> AddResult:
        """Add by free text, exactly like typing in the app."""
        body: dict[str, Any] = {"summary": summary, "description": description}
        if via is not None:
            body["via"] = via.as_dict()
        data = await self._request("POST", "/api/ha/items", body)
        if not isinstance(data, dict):
            raise QuartermasterInvalidResponseError("Unexpected response adding an item")
        return AddResult(
            items=[Item.from_dict(item) for item in data.get("items") or []],
            possible_duplicate=bool(data.get("possible_duplicate")),
        )

    async def async_update_item(
        self,
        uid: str,
        *,
        summary: str | None = None,
        status: ItemStatus | None = None,
        via: Via | None = None,
    ) -> Item:
        """Rename an item, check it off, or reopen it."""
        body: dict[str, Any] = {}
        if summary is not None:
            body["summary"] = summary
        if status is not None:
            body["status"] = str(status)
        if via is not None:
            body["via"] = via.as_dict()
        return Item.from_dict(await self._request("PATCH", f"/api/ha/items/{quote(uid, safe='')}", body))

    async def async_delete_item(self, uid: str) -> None:
        """Cancel an open item, or clear a bought one."""
        await self._request("DELETE", f"/api/ha/items/{quote(uid, safe='')}")

    async def async_stream_events(
        self, read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT
    ) -> AsyncIterator[StreamEvent]:
        """Yield events from /api/events until the connection drops.

        Never returns normally: when the stream ends this raises
        QuartermasterConnectionError (or QuartermasterAuthError for a
        rejected token), so callers can reconnect with backoff.
        """
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=DEFAULT_REQUEST_TIMEOUT, sock_read=read_timeout)
        headers = {**self._headers(accept="text/event-stream"), "Cache-Control": "no-cache"}
        try:
            async with self._session.get(f"{self.url}/api/events", headers=headers, timeout=timeout) as resp:
                await _raise_for_status(resp)
                parser = SSEParser()
                async for chunk in resp.content.iter_any():
                    for event in parser.feed(chunk):
                        yield event
        except QuartermasterError:
            raise
        except (aiohttp.ClientError, TimeoutError, asyncio.IncompleteReadError) as err:
            raise QuartermasterConnectionError(str(err) or type(err).__name__) from err
        raise QuartermasterConnectionError("Event stream closed by the server")


async def _raise_for_status(resp: aiohttp.ClientResponse) -> None:
    if resp.status < 400:
        return
    message = await _error_text(resp)
    if resp.status in (401, 403):
        raise QuartermasterAuthError(message)
    if resp.status == 404:
        raise QuartermasterNotFoundError(message)
    raise QuartermasterRequestError(resp.status, message)


async def _error_text(resp: aiohttp.ClientResponse) -> str:
    try:
        data = await resp.json(content_type=None)
    except ValueError, aiohttp.ClientError:
        data = None
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    return f"HTTP {resp.status}"
