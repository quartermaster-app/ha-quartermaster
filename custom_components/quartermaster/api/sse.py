"""Incremental parser for text/event-stream."""

from __future__ import annotations

import json

from .models import StreamEvent


class SSEParser:
    """Feed it raw bytes as they arrive; it returns complete events.

    Comment lines (heartbeats) are skipped, and events whose data isn't a JSON
    object are dropped: Quartermaster only sends JSON objects.
    """

    def __init__(self) -> None:
        """Initialize the parser."""
        self._buf = b""
        self._event = ""
        self._data: list[str] = []

    def feed(self, chunk: bytes) -> list[StreamEvent]:
        """Feed raw bytes, return the events they complete."""
        self._buf += chunk
        out: list[StreamEvent] = []
        while (idx := self._buf.find(b"\n")) >= 0:
            line = self._buf[:idx].decode("utf-8", errors="replace").rstrip("\r")
            self._buf = self._buf[idx + 1 :]
            if not line:
                if (event := self._dispatch()) is not None:
                    out.append(event)
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                self._event = value
            elif field == "data":
                self._data.append(value)
        return out

    def _dispatch(self) -> StreamEvent | None:
        event, data = self._event or "message", self._data
        self._event, self._data = "", []
        if not data:
            return None
        try:
            parsed = json.loads("\n".join(data))
        except ValueError:
            return None
        return StreamEvent(event, parsed) if isinstance(parsed, dict) else None
