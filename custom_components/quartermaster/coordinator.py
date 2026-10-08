"""Keeps the household list in sync over Quartermaster's event stream."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
import random

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Item,
    Presence,
    QuartermasterAuthError,
    QuartermasterClient,
    QuartermasterError,
    QuartermasterNotFoundError,
    QuartermasterRequestError,
    ServerStatus,
    StreamEvent,
)
from .const import (
    DEFAULT_NAME,
    DOMAIN,
    EVENT_PREFIX,
    FALLBACK_POLL_INTERVAL,
    FORWARDED_EVENTS,
    ISSUE_INCOMPATIBLE_SERVER,
    ISSUE_SERVER_UNREACHABLE,
    ISSUE_STREAM_UNAVAILABLE,
    ISSUE_UNSUPPORTED_VERSION,
    LOGGER,
    SERVER_UNREACHABLE_AFTER,
    STREAM_BACKOFF_MAX,
    STREAM_BACKOFF_MIN,
    STREAM_UNAVAILABLE_AFTER,
    SUPPORTED_HA_API,
)
from .identity import async_adopt_server_id

type QuartermasterConfigEntry = ConfigEntry[QuartermasterCoordinator]

ISSUES = (ISSUE_SERVER_UNREACHABLE, ISSUE_STREAM_UNAVAILABLE, ISSUE_UNSUPPORTED_VERSION, ISSUE_INCOMPATIBLE_SERVER)


@dataclass(frozen=True, slots=True)
class QuartermasterData:
    """Everything the entities show."""

    items: list[Item]
    presence: list[Presence]

    @property
    def shoppers(self) -> int:
        """How many household members are on a shopping trip."""
        return sum(1 for member in self.presence if member.shopping)


def device_name(status: ServerStatus) -> str:
    """Return the household's name, or "Quartermaster" when it was never set."""
    return status.household_name or DEFAULT_NAME


def issue_id(issue: str, entry: ConfigEntry) -> str:
    """Repair issue IDs are per config entry."""
    return f"{issue}_{entry.entry_id}"


class QuartermasterCoordinator(DataUpdateCoordinator[QuartermasterData]):
    """Fetches the list; refetches on `changed` events and polls only while the stream is down."""

    config_entry: QuartermasterConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: QuartermasterConfigEntry,
        client: QuartermasterClient,
        status: ServerStatus,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=FALLBACK_POLL_INTERVAL,
            # An add fires `changed` once per command; coalesce bursts
            # without the default 10 s cooldown.
            request_refresh_debouncer=Debouncer(hass, LOGGER, cooldown=0.5, immediate=True),
        )
        self.client = client
        self.status = status
        self.stream_connected = False
        self.stream_connected_at: datetime | None = None
        self.last_event_at: datetime | None = None
        self.last_stream_error: str | None = None
        self.unreachable_since: datetime | None = None
        self._stream_issue_timer: CALLBACK_TYPE | None = None
        self.async_check_server_version()

    async def _async_update_data(self) -> QuartermasterData:
        try:
            items = await self.client.async_get_items()
            presence = await self._async_get_presence()
        except QuartermasterAuthError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth") from err
        except QuartermasterError as err:
            self._async_note_unreachable()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if self.unreachable_since is not None:
            self.unreachable_since = None
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(ISSUE_SERVER_UNREACHABLE, self.config_entry))
        return QuartermasterData(items, presence)

    async def _async_get_presence(self) -> list[Presence]:
        previous = self.data.presence if self.data else []
        if self.stream_connected:
            return previous  # kept current by `presence` events
        try:
            return await self.client.async_get_presence()
        except (QuartermasterNotFoundError, QuartermasterRequestError) as err:
            LOGGER.debug("Couldn't read presence: %s", err)
            return previous

    @callback
    def _async_note_unreachable(self) -> None:
        now = dt_util.utcnow()
        if self.unreachable_since is None:
            self.unreachable_since = now
        elif now - self.unreachable_since >= SERVER_UNREACHABLE_AFTER:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id(ISSUE_SERVER_UNREACHABLE, self.config_entry),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_SERVER_UNREACHABLE,
                translation_placeholders={"url": self.client.url, "title": self.config_entry.title},
            )

    @callback
    def async_check_server_version(self) -> None:
        """Raise a repair issue when the server's Home Assistant API isn't the one this integration speaks."""
        ha_api = self.status.ha_api
        too_old = ha_api is None or ha_api < SUPPORTED_HA_API
        too_new = ha_api is not None and ha_api > SUPPORTED_HA_API
        placeholders = {
            "title": self.config_entry.title,
            "version": self.status.version,
            "ha_api": str(ha_api),
            "supported_ha_api": str(SUPPORTED_HA_API),
        }
        for issue, raise_it in ((ISSUE_UNSUPPORTED_VERSION, too_old), (ISSUE_INCOMPATIBLE_SERVER, too_new)):
            if not raise_it:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id(issue, self.config_entry))
                continue
            LOGGER.warning(
                "Quartermaster %s speaks Home Assistant API %s; this integration speaks %s",
                self.status.version,
                ha_api,
                SUPPORTED_HA_API,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id(issue, self.config_entry),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=issue,
                translation_placeholders=placeholders,
            )

    async def async_refresh_status(self) -> None:
        """Re-read /api/status (after a reconnect the server may have been upgraded or renamed)."""
        try:
            status = await self.client.async_get_status()
        except QuartermasterError as err:
            LOGGER.debug("Couldn't refresh the server status: %s", err)
            return
        if status == self.status:
            return
        self.status = status
        self.async_check_server_version()
        async_adopt_server_id(self.hass, self.config_entry, status)
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device_by_identifier(
            (DOMAIN, self.config_entry.entry_id), self.config_entry.entry_id
        ):
            registry.async_update_device(device.id, name=device_name(status), sw_version=status.version)

    @callback
    def async_start_stream(self) -> None:
        """Start the event stream listener; the entry cancels it on unload."""
        self.config_entry.async_create_background_task(
            self.hass, self._async_stream_loop(), f"{DOMAIN}_events_{self.config_entry.entry_id}"
        )
        self._async_schedule_stream_issue()
        self.config_entry.async_on_unload(self._async_cancel_stream_issue_timer)

    async def _async_stream_loop(self) -> None:
        backoff = STREAM_BACKOFF_MIN
        while True:
            try:
                async for event in self.client.async_stream_events():
                    if not self.stream_connected:
                        self._async_set_stream_state(True)
                        backoff = STREAM_BACKOFF_MIN
                    self._async_handle_event(event)
            except QuartermasterAuthError as err:
                LOGGER.warning("Quartermaster rejected the token on the event stream: %s", err)
                self._async_set_stream_state(False, str(err))
                self.config_entry.async_start_reauth(self.hass)
                return
            except QuartermasterError as err:
                self._async_set_stream_state(False, str(err))
            except Exception as err:  # noqa: BLE001  # keep the listener alive whatever happens
                LOGGER.exception("Unexpected error in the Quartermaster event stream")
                self._async_set_stream_state(False, repr(err))
            delay = backoff + random.uniform(0, backoff / 2)  # noqa: S311
            LOGGER.debug("Event stream down (%s); reconnecting in %.1f s", self.last_stream_error, delay)
            await asyncio.sleep(delay)
            backoff = min(backoff * 2, STREAM_BACKOFF_MAX)

    @callback
    def _async_set_stream_state(self, connected: bool, error: str | None = None) -> None:
        if connected == self.stream_connected:
            self.last_stream_error = error or self.last_stream_error
            return
        self.stream_connected = connected
        if connected:
            LOGGER.info("Quartermaster event stream connected; live updates on")
            self.stream_connected_at = dt_util.utcnow()
            self.last_stream_error = None
            # Changes now arrive as events, so stop polling.
            self.update_interval = None
            self._async_cancel_stream_issue_timer()
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(ISSUE_STREAM_UNAVAILABLE, self.config_entry))
            self.config_entry.async_create_task(self.hass, self.async_refresh_status())
        else:
            LOGGER.info(
                "Quartermaster event stream lost (%s); polling every %s s until it reconnects",
                error,
                int(FALLBACK_POLL_INTERVAL.total_seconds()),
            )
            self.last_stream_error = error
            self.update_interval = FALLBACK_POLL_INTERVAL
            self._async_schedule_stream_issue()
            # Refresh now; the coordinator then keeps polling at the interval.
            self.config_entry.async_create_task(self.hass, self.async_request_refresh())
        self.async_update_listeners()

    @callback
    def _async_schedule_stream_issue(self) -> None:
        self._async_cancel_stream_issue_timer()
        self._stream_issue_timer = async_call_later(self.hass, STREAM_UNAVAILABLE_AFTER, self._async_stream_issue_due)

    @callback
    def _async_cancel_stream_issue_timer(self) -> None:
        if self._stream_issue_timer is not None:
            self._stream_issue_timer()
            self._stream_issue_timer = None

    @callback
    def _async_stream_issue_due(self, _now: datetime) -> None:
        """Raise a repair if the stream is still down while the server itself answers."""
        self._stream_issue_timer = None
        if self.stream_connected or not self.last_update_success:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id(ISSUE_STREAM_UNAVAILABLE, self.config_entry),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_STREAM_UNAVAILABLE,
            translation_placeholders={
                "title": self.config_entry.title,
                "error": self.last_stream_error or "no events received",
            },
        )

    @callback
    def _async_handle_event(self, event: StreamEvent) -> None:
        self.last_event_at = dt_util.utcnow()
        if event.event == "changed":
            # Also sent on every connect, so a reconnect always resyncs.
            self.config_entry.async_create_task(self.hass, self.async_request_refresh())
        elif event.event == "presence":
            try:
                presence = Presence.list_from(event.data.get("users"))
            except QuartermasterError:
                LOGGER.debug("Ignoring a malformed presence event: %s", event.data)
                return
            # The stream starts after the first refresh, so data is always set.
            self.async_set_updated_data(replace(self.data, presence=presence))
        elif event.event in FORWARDED_EVENTS:
            data = {key: value for key, value in event.data.items() if key != "type"}
            self.hass.bus.async_fire(f"{EVENT_PREFIX}{event.event}", data)
