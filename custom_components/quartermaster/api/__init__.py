"""Async client for the Quartermaster Home Assistant API.

Has no Home Assistant imports, so it can be tested on its own and split out
into a separate library later.
"""

from .client import QuartermasterClient, normalize_url
from .errors import (
    QuartermasterAuthError,
    QuartermasterConnectionError,
    QuartermasterError,
    QuartermasterInvalidResponseError,
    QuartermasterNotFoundError,
    QuartermasterRequestError,
)
from .models import (
    AddResult,
    Item,
    ItemStatus,
    Presence,
    ServerStatus,
    StreamEvent,
    Via,
    parse_version,
)
from .sse import SSEParser

__all__ = [
    "AddResult",
    "Item",
    "ItemStatus",
    "Presence",
    "QuartermasterAuthError",
    "QuartermasterClient",
    "QuartermasterConnectionError",
    "QuartermasterError",
    "QuartermasterInvalidResponseError",
    "QuartermasterNotFoundError",
    "QuartermasterRequestError",
    "SSEParser",
    "ServerStatus",
    "StreamEvent",
    "Via",
    "normalize_url",
    "parse_version",
]
