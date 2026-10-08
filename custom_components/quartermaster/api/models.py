"""Data returned by the Quartermaster API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Literal

from .errors import QuartermasterInvalidResponseError

# The household name a server uses when setup didn't name one.
DEFAULT_HOUSEHOLD = "Household"


class ItemStatus(StrEnum):
    """Whether an item still needs buying."""

    NEEDS_ACTION = "needs_action"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class Item:
    """One request on the household list, flattened for to-do lists."""

    uid: str
    summary: str
    description: str | None
    status: ItemStatus

    @classmethod
    def from_dict(cls, data: Any) -> Item:
        """Build from an API item."""
        if not isinstance(data, dict) or not isinstance(data.get("uid"), str):
            raise QuartermasterInvalidResponseError("Item without a uid")
        description = data.get("description")
        try:
            status = ItemStatus(data.get("status") or ItemStatus.NEEDS_ACTION)
        except ValueError:
            status = ItemStatus.NEEDS_ACTION
        return cls(
            uid=data["uid"],
            summary=str(data.get("summary") or ""),
            description=str(description) if description else None,
            status=status,
        )


@dataclass(frozen=True, slots=True)
class AddResult:
    """What adding by free text created."""

    items: list[Item]
    possible_duplicate: bool


@dataclass(frozen=True, slots=True)
class Via:
    """Who or what an add or change came from, as shown in Quartermaster."""

    kind: Literal["satellite", "user"]
    name: str

    def as_dict(self) -> dict[str, str]:
        """Return the JSON form."""
        return {"kind": self.kind, "name": self.name}

    def __str__(self) -> str:
        """Short form for logs."""
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True, slots=True)
class ServerStatus:
    """The unauthenticated GET /api/status answer."""

    version: str
    protocol: int | None
    setup_required: bool
    household: str
    # Stable for the life of the server's database; None on servers before it existed.
    server_id: str | None = None
    # Version of the Home Assistant API; None on servers before it existed.
    ha_api: int | None = None

    @classmethod
    def from_dict(cls, data: Any) -> ServerStatus:
        """Build from the status JSON; raise if it isn't a Quartermaster server."""
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise QuartermasterInvalidResponseError("Not a Quartermaster server")
        protocol = data.get("protocol")
        household = data.get("household")
        return cls(
            version=data["version"],
            protocol=_int_or_none(protocol),
            setup_required=bool(data.get("setup_required")),
            household=household.strip() if isinstance(household, str) else "",
            server_id=_str_or_none(data.get("server_id")),
            ha_api=_int_or_none(data.get("ha_api")),
        )

    @property
    def household_name(self) -> str | None:
        """The household's name, or None when it was never set."""
        if not self.household or self.household == DEFAULT_HOUSEHOLD:
            return None
        return self.household


@dataclass(frozen=True, slots=True)
class Presence:
    """One household member, and the trip they're on if they're shopping."""

    user_id: str
    last_sync_at: str | None
    trip_id: str | None
    store_id: str | None

    @property
    def shopping(self) -> bool:
        """True while the member is on a shopping trip."""
        return self.trip_id is not None

    @classmethod
    def from_dict(cls, data: Any) -> Presence:
        """Build from one entry of a presence list."""
        if not isinstance(data, dict):
            raise QuartermasterInvalidResponseError("Bad presence entry")
        trip = data.get("trip")
        trip = trip if isinstance(trip, dict) else {}
        return cls(
            user_id=str(data.get("user_id") or ""),
            last_sync_at=data.get("last_sync_at") or None,
            trip_id=trip.get("id") or None,
            store_id=trip.get("store_id") or None,
        )

    @classmethod
    def list_from(cls, data: Any) -> list[Presence]:
        """Build from a presence list."""
        if not isinstance(data, list):
            raise QuartermasterInvalidResponseError("Presence is not a list")
        return [cls.from_dict(entry) for entry in data]


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One Server-Sent Event from /api/events."""

    event: str
    data: dict[str, Any]


def parse_version(version: str) -> tuple[int, ...]:
    """Turn "0.2.1" (or "v0.2.1-beta") into (0, 2, 1) for comparisons."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", version.strip())
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _str_or_none(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
