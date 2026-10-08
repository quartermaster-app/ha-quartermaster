"""To-do entity: Home Assistant actions and intents map to the Quartermaster API."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    intent,
)
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.quartermaster import attribution
from custom_components.quartermaster.const import DOMAIN

from .conftest import ENTITY, HOUSEHOLD, URL, FakeStream, calls, setup_entry


async def setup_with_intents(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up intents and the entry, then let the hook retry run once.

    On a fresh install the todo intents can register after our entry loads;
    the integration retries every 2 s until it has wrapped them.
    """
    assert await async_setup_component(hass, "intent", {})
    await setup_entry(hass, entry)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()


@pytest.fixture
def write_api(mock_api: AiohttpClientMocker) -> AiohttpClientMocker:
    """Accept writes."""
    mock_api.post(f"{URL}/api/ha/items", json={"items": [], "possible_duplicate": False})
    for uid, summary in (("req-1", "Eggs"), ("req-2", "Bread")):
        mock_api.patch(
            f"{URL}/api/ha/items/{uid}",
            json={"uid": uid, "summary": summary, "description": None, "status": "completed"},
        )
        mock_api.delete(f"{URL}/api/ha/items/{uid}", json={"ok": True})
    return mock_api


async def call(hass: HomeAssistant, service: str, data: dict[str, Any], **kwargs: Any) -> Any:
    """Call a todo action on the list."""
    return await hass.services.async_call("todo", service, data, target={"entity_id": ENTITY}, blocking=True, **kwargs)


async def test_entity(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """One list named after the household; state is the open count."""
    await setup_entry(hass, config_entry)
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "1"
    assert state.attributes["friendly_name"] == HOUSEHOLD
    entry = er.async_get(hass).async_get(ENTITY)
    assert entry is not None
    assert entry.unique_id == config_entry.entry_id
    assert entry.translation_key == "shopping_list"

    result = await hass.services.async_call(
        "todo", "get_items", {"entity_id": ENTITY}, blocking=True, return_response=True
    )
    items = result[ENTITY]["items"]  # type: ignore[index]
    assert [(i["uid"], i["summary"], i["status"], i.get("description")) for i in items] == [
        ("req-1", "Eggs", "needs_action", "2 dozen"),
        ("req-2", "Bread", "completed", None),
    ]


async def test_add_from_automation(
    hass: HomeAssistant, config_entry: MockConfigEntry, write_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """An action with no user sends no `via`, then the list refreshes."""
    await setup_entry(hass, config_entry)
    before = len(calls(write_api, "GET", "/api/ha/items"))
    await call(hass, "add_item", {"item": "milk and bread"})
    assert calls(write_api, "POST") == [(f"{URL}/api/ha/items", {"summary": "milk and bread", "description": None})]
    assert len(calls(write_api, "GET", "/api/ha/items")) == before + 1


async def test_add_by_user(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    write_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    hass_admin_user: MockUser,
) -> None:
    """An action called by a logged-in user is credited to that user."""
    await setup_entry(hass, config_entry)
    await call(hass, "add_item", {"item": "Milk"}, context=Context(user_id=hass_admin_user.id))
    assert calls(write_api, "POST")[0][1]["via"] == {"kind": "user", "name": hass_admin_user.name}


async def test_add_by_system_or_unknown_user(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    write_api: AiohttpClientMocker,
    fake_stream: FakeStream,
) -> None:
    """System users and unknown user IDs aren't credited."""
    await setup_entry(hass, config_entry)
    system = await hass.auth.async_create_system_user("Supervisor")
    assert await attribution._user_name(hass, Context(user_id=system.id)) is None
    assert await attribution._user_name(hass, Context(user_id="missing")) is None


async def test_stale_context_is_ignored(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    write_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    hass_admin_user: MockUser,
) -> None:
    """A user context older than Home Assistant's 5 s window isn't used."""
    await setup_entry(hass, config_entry)
    entity = hass.data["todo"].get_entity(ENTITY)
    entity.async_set_context(Context(user_id=hass_admin_user.id))
    entity._context_set -= 10
    assert entity._action_context() is None


async def test_add_empty(
    hass: HomeAssistant, config_entry: MockConfigEntry, write_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """Blank text is a validation error, not a request."""
    await setup_entry(hass, config_entry)
    entity = hass.data["todo"].get_entity(ENTITY)
    from homeassistant.components.todo import TodoItem  # noqa: PLC0415

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_create_todo_item(TodoItem(summary="  "))
    assert err.value.translation_key == "empty_item"
    assert calls(write_api, "POST") == []


async def test_update(
    hass: HomeAssistant, config_entry: MockConfigEntry, write_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """Checking off sends status, renaming sends summary; no-ops send nothing."""
    await setup_entry(hass, config_entry)
    await call(hass, "update_item", {"item": "Eggs", "status": "completed"})
    await call(hass, "update_item", {"item": "req-1", "rename": "Duck eggs"})
    await call(hass, "update_item", {"item": "Bread", "status": "needs_action"})
    await call(hass, "update_item", {"item": "Bread", "status": "completed"})
    assert calls(write_api, "PATCH") == [
        (f"{URL}/api/ha/items/req-1", {"status": "completed"}),
        (f"{URL}/api/ha/items/req-1", {"summary": "Duck eggs"}),
        (f"{URL}/api/ha/items/req-2", {"status": "needs_action"}),
    ]


async def test_update_unknown_uid(
    hass: HomeAssistant, config_entry: MockConfigEntry, write_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """An item that isn't on the list is a validation error."""
    await setup_entry(hass, config_entry)
    entity = hass.data["todo"].get_entity(ENTITY)
    from homeassistant.components.todo import TodoItem  # noqa: PLC0415

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_update_todo_item(TodoItem(uid="nope", summary="x"))
    assert err.value.translation_key == "item_not_found"


async def test_delete(
    hass: HomeAssistant, config_entry: MockConfigEntry, write_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """Removing items deletes each by uid."""
    await setup_entry(hass, config_entry)
    await call(hass, "remove_item", {"item": ["Eggs", "Bread"]})
    assert [url for url, _ in calls(write_api, "DELETE")] == [
        f"{URL}/api/ha/items/req-1",
        f"{URL}/api/ha/items/req-2",
    ]


@pytest.mark.parametrize(
    ("response", "error", "key"),
    [
        ({"status": 400, "json": {"error": "summary required"}}, ServiceValidationError, "request_rejected"),
        ({"status": 500, "json": {"error": "database locked"}}, HomeAssistantError, "server_error"),
        ({"exc": TimeoutError()}, HomeAssistantError, "cannot_connect"),
        ({"status": 401, "json": {"error": "Not signed in"}}, HomeAssistantError, "invalid_auth"),
    ],
)
async def test_action_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    response: dict[str, Any],
    error: type[HomeAssistantError],
    key: str,
) -> None:
    """Server errors become translated Home Assistant errors; the list still refreshes."""
    mock_api.post(f"{URL}/api/ha/items", **response)
    await setup_entry(hass, config_entry)
    before = len(calls(mock_api, "GET", "/api/ha/items"))
    with pytest.raises(error) as err:
        await call(hass, "add_item", {"item": "x"})
    assert type(err.value) is error
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == key
    assert len(calls(mock_api, "GET", "/api/ha/items")) == before + 1
    reauth = [
        f for f in hass.config_entries.flow.async_progress_by_handler(DOMAIN) if f["context"]["source"] == "reauth"
    ]
    assert bool(reauth) == (key == "invalid_auth")


@pytest.fixture
async def kitchen_satellite(hass: HomeAssistant) -> tuple[str, str]:
    """A satellite entity on a device in the Kitchen area."""
    other = MockConfigEntry(domain="esphome")
    other.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("esphome", "sat1")}, name="Voice PE"
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    entity = er.async_get(hass).async_get_or_create("assist_satellite", "esphome", "sat1", device_id=device.id)
    return entity.entity_id, device.id


async def add_by_intent(hass: HomeAssistant, **kwargs: Any) -> intent.IntentResponse:
    """Run HassListAddItem against the list, as Assist would."""
    return await intent.async_handle(
        hass,
        "test",
        "HassListAddItem",
        {"item": {"value": "oat milk"}, "name": {"value": HOUSEHOLD}},
        **kwargs,
    )


async def test_intent_from_satellite(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    write_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    kitchen_satellite: tuple[str, str],
) -> None:
    """HassListAddItem from a satellite is credited to its area."""
    await setup_with_intents(hass, config_entry)
    satellite_id, device_id = kitchen_satellite
    response = await add_by_intent(hass, satellite_id=satellite_id, device_id=device_id)
    assert response.response_type is intent.IntentResponseType.ACTION_DONE
    assert calls(write_api, "POST") == [
        (
            f"{URL}/api/ha/items",
            {"summary": "Oat milk", "description": None, "via": {"kind": "satellite", "name": "Kitchen"}},
        )
    ]


async def test_satellite_names(hass: HomeAssistant) -> None:
    """Area of the entity, then its device, then the entity name, then the state name."""
    other = MockConfigEntry(domain="esphome")
    other.add_to_hass(hass)
    areas, devices, entities = ar.async_get(hass), dr.async_get(hass), er.async_get(hass)
    hall = areas.async_create("Hall")
    device = devices.async_get_or_create(config_entry_id=other.entry_id, identifiers={("esphome", "d")}, name="Puck")
    in_hall = entities.async_get_or_create("assist_satellite", "esphome", "a", device_id=device.id)
    entities.async_update_entity(in_hall.entity_id, name="Hall satellite", area_id=hall.id)
    on_device = entities.async_get_or_create("assist_satellite", "esphome", "b", device_id=device.id)
    named = entities.async_get_or_create("assist_satellite", "esphome", "c", original_name="Porch")
    hass.states.async_set("assist_satellite.loose", "idle", {"friendly_name": "Loose one"})

    assert attribution._satellite_name(hass, in_hall.entity_id, None) == "Hall"
    assert attribution._satellite_name(hass, on_device.entity_id, None) == "Puck"
    assert attribution._satellite_name(hass, named.entity_id, None) == "Porch"
    assert attribution._satellite_name(hass, "assist_satellite.loose", None) == "Loose one"
    assert attribution._satellite_name(hass, "assist_satellite.gone", "missing-device") is None


async def test_intent_from_device_without_area(
    hass: HomeAssistant, config_entry: MockConfigEntry, write_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """A device with no area is named by its device name."""
    await setup_with_intents(hass, config_entry)
    other = MockConfigEntry(domain="esphome")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("esphome", "sat2")}, name="Garage Voice"
    )
    await add_by_intent(hass, device_id=device.id)
    assert calls(write_api, "POST")[0][1]["via"] == {"kind": "satellite", "name": "Garage Voice"}


async def test_intent_from_user(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    write_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    hass_admin_user: MockUser,
) -> None:
    """Assist typed in the app carries the user in the context."""
    await setup_with_intents(hass, config_entry)
    await add_by_intent(hass, context=Context(user_id=hass_admin_user.id))
    assert calls(write_api, "POST")[0][1]["via"] == {"kind": "user", "name": hass_admin_user.name}


async def test_intent_unattributed(
    hass: HomeAssistant, config_entry: MockConfigEntry, write_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """An intent with no satellite, user or known device is credited to the integration."""
    await setup_with_intents(hass, config_entry)
    await add_by_intent(hass, device_id="unknown-device")
    assert calls(write_api, "POST")[0][1] == {"summary": "Oat milk", "description": None}


async def test_complete_intent_attributed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    write_api: AiohttpClientMocker,
    fake_stream: FakeStream,
    kitchen_satellite: tuple[str, str],
) -> None:
    """HassListCompleteItem passes the satellite along too."""
    await setup_with_intents(hass, config_entry)
    satellite_id, device_id = kitchen_satellite
    await intent.async_handle(
        hass,
        "test",
        "HassListCompleteItem",
        {"item": {"value": "eggs"}, "name": {"value": HOUSEHOLD}},
        satellite_id=satellite_id,
        device_id=device_id,
    )
    assert calls(write_api, "PATCH") == [
        (f"{URL}/api/ha/items/req-1", {"status": "completed", "via": {"kind": "satellite", "name": "Kitchen"}})
    ]


async def test_intent_hooks_shared_and_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """Hooks stay while any entry is loaded and are removed with the last one."""
    other_url = "http://other.test"
    mock_api.get(f"{other_url}/api/status", json={"version": "0.1.0", "household": "Cabin"})
    mock_api.get(f"{other_url}/api/ha/items", json=[])
    mock_api.get(f"{other_url}/api/presence", json=[])
    other = MockConfigEntry(domain=DOMAIN, unique_id=other_url, data={"url": other_url, "token": "qm_o"})

    await setup_with_intents(hass, config_entry)
    await setup_entry(hass, other)
    handler = next(h for h in intent.async_get(hass) if h.intent_type == "HassListAddItem")
    assert "async_handle" in vars(handler)
    assert attribution.async_intent_hooks_ready(hass)
    # Installing again doesn't double-wrap.
    assert attribution.async_install_intent_hooks(hass) == 0

    assert await hass.config_entries.async_unload(other.entry_id)
    assert "async_handle" in vars(handler)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert "async_handle" not in vars(handler)
    assert attribution.async_wrapped_intents(hass) == []


async def test_intent_hooks_give_up(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """Without the intent integration, the retries stop after a minute."""
    await setup_entry(hass, config_entry)
    for _ in range(attribution.HOOK_RETRIES + 1):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2.1))
        await hass.async_block_till_done()
    assert not attribution.async_intent_hooks_ready(hass)


async def test_items_gone_meanwhile(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: AiohttpClientMocker, fake_stream: FakeStream
) -> None:
    """An item removed in the app meanwhile (404) isn't an error: the list just refreshes."""
    for method in (mock_api.patch, mock_api.delete):
        method(f"{URL}/api/ha/items/req-1", status=404, json={"error": "No such item"})
    mock_api.delete(f"{URL}/api/ha/items/req-2", json={"ok": True})
    await setup_entry(hass, config_entry)
    before = len(calls(mock_api, "GET", "/api/ha/items"))
    await call(hass, "update_item", {"item": "Eggs", "status": "completed"})
    # Let the refresh debouncer's cooldown pass.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    await call(hass, "remove_item", {"item": ["Eggs", "Bread"]})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert [url for url, _ in calls(mock_api, "DELETE")] == [f"{URL}/api/ha/items/req-1", f"{URL}/api/ha/items/req-2"]
    assert len(calls(mock_api, "GET", "/api/ha/items")) == before + 2
