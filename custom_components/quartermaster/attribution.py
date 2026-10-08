"""Work out who or what added an item, for Quartermaster's `via` field.

TodoListEntity.async_create_todo_item only receives a TodoItem; Home
Assistant doesn't pass the caller along. So:

* Voice and Assist: the todo intents (HassListAddItem, HassListCompleteItem)
  call the entity directly. The Intent object they hold carries
  `satellite_id`, `device_id` and `context` (whose `user_id` is set when a
  logged-in user typed or spoke in the app). We wrap those two handlers'
  `async_handle` so the Intent is visible to the entity through a
  ContextVar. The wrapper only stores the Intent and then calls Home
  Assistant's handler unchanged, so other to-do lists behave exactly as
  before. If the handlers can't be found or wrapped, adds still work; they
  just aren't attributed.
* Actions (todo.add_item and friends): Home Assistant sets the caller's
  Context on the entity just before calling it, so a user acting from the
  UI is known. Automations and scripts carry no user and stay unattributed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime, timedelta

from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    intent,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.util.hass_dict import HassKey

from .api import Via
from .const import DOMAIN, LOGGER
from .coordinator import QuartermasterConfigEntry

WRAPPED_INTENTS = ("HassListAddItem", "HassListCompleteItem")
_MARKER = "_quartermaster_original_async_handle"
DATA_HOOK_USERS: HassKey[int] = HassKey(f"{DOMAIN}_intent_hook_users")

# The intent integration can register its handlers after we load (on a fresh
# install the todo platform loads together with us), so keep trying briefly.
HOOK_RETRY_INTERVAL = timedelta(seconds=2)
HOOK_RETRIES = 30

type IntentHandle = Callable[[intent.Intent], Awaitable[intent.IntentResponse]]

current_intent: ContextVar[intent.Intent | None] = ContextVar("quartermaster_current_intent", default=None)


@callback
def async_install_intent_hooks(hass: HomeAssistant) -> int:
    """Wrap the todo intent handlers that are registered now. Returns how many were wrapped."""
    wrapped = 0
    for handler in list(intent.async_get(hass)):
        if handler.intent_type not in WRAPPED_INTENTS or hasattr(handler, _MARKER):
            continue
        original: IntentHandle = handler.async_handle

        async def async_handle(intent_obj: intent.Intent, _original: IntentHandle = original) -> intent.IntentResponse:
            token = current_intent.set(intent_obj)
            try:
                return await _original(intent_obj)
            finally:
                current_intent.reset(token)

        setattr(handler, _MARKER, original)
        handler.async_handle = async_handle  # type: ignore[method-assign]
        wrapped += 1
    if wrapped:
        LOGGER.debug("Wrapped %s todo intent handler(s) for attribution", wrapped)
    return wrapped


@callback
def async_wrapped_intents(hass: HomeAssistant) -> list[str]:
    """Return the todo intents that are currently wrapped."""
    return sorted(h.intent_type for h in intent.async_get(hass) if hasattr(h, _MARKER))


@callback
def async_intent_hooks_ready(hass: HomeAssistant) -> bool:
    """Return True when every todo intent we care about is registered and wrapped."""
    return set(async_wrapped_intents(hass)) >= set(WRAPPED_INTENTS)


@callback
def async_remove_intent_hooks(hass: HomeAssistant) -> None:
    """Restore Home Assistant's own todo intent handlers."""
    for handler in list(intent.async_get(hass)):
        if hasattr(handler, _MARKER):
            delattr(handler, _MARKER)
            # Drop the instance attribute so the class method applies again.
            vars(handler).pop("async_handle", None)


@callback
def async_setup_intent_hooks(hass: HomeAssistant, entry: QuartermasterConfigEntry) -> None:
    """Wrap the todo intents while this entry is loaded (shared between entries)."""
    hass.data[DATA_HOOK_USERS] = hass.data.get(DATA_HOOK_USERS, 0) + 1
    async_install_intent_hooks(hass)

    @callback
    def _release() -> None:
        users = hass.data.get(DATA_HOOK_USERS, 0) - 1
        hass.data[DATA_HOOK_USERS] = max(users, 0)
        if users <= 0:
            async_remove_intent_hooks(hass)

    entry.async_on_unload(_release)

    @callback
    def _started(hass: HomeAssistant) -> None:
        async_install_intent_hooks(hass)

    entry.async_on_unload(async_at_started(hass, _started))

    tries = 0

    @callback
    def _retry(_now: datetime) -> None:
        nonlocal tries
        tries += 1
        async_install_intent_hooks(hass)
        if async_intent_hooks_ready(hass) or tries >= HOOK_RETRIES:
            cancel()

    cancel = async_track_time_interval(hass, _retry, HOOK_RETRY_INTERVAL, cancel_on_shutdown=True)
    entry.async_on_unload(cancel)


async def async_resolve_via(hass: HomeAssistant, action_context: Context | None) -> Via | None:
    """Return Quartermaster's `via` for the current call, or None to credit the integration.

    `action_context` is the context Home Assistant set on the entity for an
    action call, if it is recent; it's ignored when an intent is running.
    """
    if (intent_obj := current_intent.get()) is not None:
        if intent_obj.satellite_id and (name := _satellite_name(hass, intent_obj.satellite_id, intent_obj.device_id)):
            return Via("satellite", name)
        if user := await _user_name(hass, intent_obj.context):
            return Via("user", user)
        # A device without a satellite entity (a voice device that runs its
        # own pipeline, or an LLM tool call that only knows the device).
        if intent_obj.device_id and (name := _device_name(hass, intent_obj.device_id)):
            return Via("satellite", name)
        return None
    if user := await _user_name(hass, action_context):
        return Via("user", user)
    return None


async def _user_name(hass: HomeAssistant, context: Context | None) -> str | None:
    if context is None or not context.user_id:
        return None
    user = await hass.auth.async_get_user(context.user_id)
    if user is None or user.system_generated or not user.name:
        return None
    return user.name


def _satellite_name(hass: HomeAssistant, satellite_id: str, device_id: str | None) -> str | None:
    """Name a satellite by its area ("Kitchen"), else by its device or entity name."""
    entry = er.async_get(hass).async_get(satellite_id)
    device_id = device_id or (entry.device_id if entry else None)
    if entry is not None and entry.area_id and (area := ar.async_get(hass).async_get_area(entry.area_id)):
        return area.name
    if device_id and (name := _device_name(hass, device_id)):
        return name
    if entry is not None and (name := entry.name or entry.original_name):
        return name
    if state := hass.states.get(satellite_id):
        return state.name
    return None


def _device_name(hass: HomeAssistant, device_id: str) -> str | None:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    if device.area_id and (area := ar.async_get(hass).async_get_area(device.area_id)):
        return area.name
    return device.name_by_user or device.name
