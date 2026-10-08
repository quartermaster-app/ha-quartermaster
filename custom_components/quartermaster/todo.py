"""The household list as a Home Assistant to-do list."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import time

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity import CONTEXT_RECENT_TIME_SECONDS
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import (
    Item,
    ItemStatus,
    QuartermasterAuthError,
    QuartermasterError,
    QuartermasterNotFoundError,
    QuartermasterRequestError,
)
from .attribution import async_resolve_via
from .const import DOMAIN, LOGGER
from .coordinator import QuartermasterConfigEntry, QuartermasterCoordinator
from .entity import QuartermasterEntity

# Writes go straight to the server, which handles concurrent changes itself.
PARALLEL_UPDATES = 0

_TO_HA = {ItemStatus.NEEDS_ACTION: TodoItemStatus.NEEDS_ACTION, ItemStatus.COMPLETED: TodoItemStatus.COMPLETED}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the household list."""
    async_add_entities([QuartermasterTodoListEntity(entry.runtime_data)])


class QuartermasterTodoListEntity(QuartermasterEntity, TodoListEntity):
    """One to-do list per Quartermaster household, named after the household."""

    _attr_name = None
    _attr_translation_key = "shopping_list"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(self, coordinator: QuartermasterCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator, None)

    @property
    def todo_items(self) -> list[TodoItem]:
        """The household list, open items first as the server orders them."""
        return [
            TodoItem(uid=item.uid, summary=item.summary, status=_TO_HA[item.status], description=item.description)
            for item in self.coordinator.data.items
        ]

    def _find(self, uid: str) -> Item | None:
        return next((item for item in self.coordinator.data.items if item.uid == uid), None)

    def _action_context(self) -> Context | None:
        """Return the caller's context for an action call, if Home Assistant set it just now."""
        if self._context_set is None or time.time() - self._context_set > CONTEXT_RECENT_TIME_SECONDS:
            return None
        return self._context

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add by free text, exactly like typing in the app."""
        if not item.summary or not item.summary.strip():
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="empty_item")
        via = await async_resolve_via(self.hass, self._action_context())
        LOGGER.debug("Adding %r via %s", item.summary, via or "integration")
        async with self._refresh_after():
            await self.coordinator.client.async_add_item(item.summary, item.description, via)

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Rename, check off or reopen an item. Descriptions and due dates aren't stored."""
        uid = item.uid or ""
        current = self._find(uid)
        if current is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="item_not_found", translation_placeholders={"item": uid}
            )
        summary = item.summary if item.summary and item.summary != current.summary else None
        # The action passes a plain string here, the intent a TodoItemStatus.
        status = ItemStatus(str(item.status)) if item.status is not None else None
        if status == current.status:
            status = None
        if summary is None and status is None:
            return
        via = await async_resolve_via(self.hass, self._action_context())
        async with self._refresh_after():
            try:
                await self.coordinator.client.async_update_item(uid, summary=summary, status=status, via=via)
            except QuartermasterNotFoundError:
                # Removed, cleared or merged in the app meanwhile; the refresh drops it.
                LOGGER.debug("Item %s is no longer on the list", uid)

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Cancel open items, clear bought ones."""
        async with self._refresh_after():
            for uid in uids:
                try:
                    await self.coordinator.client.async_delete_item(uid)
                except QuartermasterNotFoundError:
                    LOGGER.debug("Item %s was already gone", uid)

    @asynccontextmanager
    async def _refresh_after(self) -> AsyncIterator[None]:
        """Turn client errors into Home Assistant errors, then refresh the list either way."""
        try:
            yield
        except QuartermasterAuthError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="invalid_auth") from err
        except QuartermasterRequestError as err:
            if err.status < 500:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="request_rejected",
                    translation_placeholders={"error": err.message},
                ) from err
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="server_error",
                translation_placeholders={"error": err.message},
            ) from err
        except QuartermasterError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()
