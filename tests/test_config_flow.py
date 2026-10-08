"""Config flow tests: setup, reauthentication and reconfiguration."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.quartermaster.const import DOMAIN

from .conftest import HOUSEHOLD, ITEMS, STATUS, TOKEN, URL

NEW_URL = "https://shopping.example.com"


@pytest.fixture(autouse=True)
def no_setup() -> Generator[AsyncMock]:
    """Don't set up the entry after the flow creates or reloads it."""
    with patch("custom_components.quartermaster.async_setup_entry", return_value=True) as mock:
        yield mock


def healthy(aioclient_mock: AiohttpClientMocker, url: str = URL, status: dict[str, Any] = STATUS) -> None:
    """Answer like a working server."""
    aioclient_mock.get(f"{url}/api/status", json=status)
    aioclient_mock.get(f"{url}/api/ha/items", json=ITEMS)


async def start(hass: HomeAssistant) -> Any:
    """Open the user step."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """URL and token are checked; the entry is named after the household."""
    healthy(aioclient_mock)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": f" {URL}/ ", "token": " qm_abc ", "verify_ssl": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOUSEHOLD
    assert result["data"] == {"url": URL, "token": "qm_abc", "verify_ssl": False}
    assert result["result"].unique_id == URL

    (_, status_url, _, status_headers), (_, items_url, _, items_headers) = aioclient_mock.mock_calls
    assert (str(status_url), str(items_url)) == (f"{URL}/api/status", f"{URL}/api/ha/items")
    assert "Authorization" not in status_headers
    assert items_headers["Authorization"] == "Bearer qm_abc"


async def test_user_flow_unnamed_household(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A household that was never named shows up as Quartermaster."""
    healthy(aioclient_mock, status={**STATUS, "household": "Household"})
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "token": TOKEN, "verify_ssl": True}
    )
    assert result["title"] == "Quartermaster"


@pytest.mark.parametrize(
    ("mock", "errors"),
    [
        ({"status": {"exc": aiohttp.ClientConnectionError()}}, {"base": "cannot_connect"}),
        ({"status": {"text": "<html>"}}, {"base": "not_quartermaster"}),
        ({"status": {"json": {"version": "0.1.0", "setup_required": True}}}, {"base": "setup_required"}),
        ({"items": {"status": 401, "json": {"error": "Not signed in"}}}, {"base": "invalid_auth"}),
        ({"items": {"status": 403, "json": {"error": "This token cannot do that"}}}, {"base": "invalid_auth"}),
        ({"items": {"status": 500, "json": {"error": "boom"}}}, {"base": "cannot_connect"}),
        ({"items": {"exc": ValueError("bug")}}, {"base": "unknown"}),
    ],
)
async def test_user_flow_errors_then_recover(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock: dict[str, dict[str, Any]], errors: dict[str, str]
) -> None:
    """Each failure shows its error and keeps the form, and a retry can succeed."""
    aioclient_mock.get(f"{URL}/api/status", **mock.get("status", {"json": STATUS}))
    aioclient_mock.get(f"{URL}/api/ha/items", **mock.get("items", {"json": ITEMS}))
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "token": TOKEN, "verify_ssl": True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    aioclient_mock.clear_requests()
    healthy(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "token": TOKEN, "verify_ssl": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_invalid_url(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """An address without a scheme is rejected before any request."""
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "qm.test", "token": TOKEN, "verify_ssl": True}
    )
    assert result["errors"] == {"url": "invalid_url"}
    assert aioclient_mock.call_count == 0


async def test_user_flow_already_configured(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """The same server can't be added twice, whatever the case or trailing slash."""
    config_entry.add_to_hass(hass)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "HTTP://QM.test/", "token": TOKEN, "verify_ssl": True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry) -> None:
    """Reauth checks the new token before saving it."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["url"] == URL

    aioclient_mock.get(f"{URL}/api/status", json=STATUS)
    aioclient_mock.get(f"{URL}/api/ha/items", status=401, json={"error": "Not signed in"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": "qm_still_bad"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    healthy(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"token": " qm_new "})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == {"url": URL, "token": "qm_new", "verify_ssl": True}


async def test_reconfigure_new_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Moving the server updates the address and unique ID and keeps the token if none is given."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    aioclient_mock.get(f"{NEW_URL}/api/status", exc=aiohttp.ClientConnectionError())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": NEW_URL, "verify_ssl": False})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    healthy(aioclient_mock, NEW_URL)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": f"{NEW_URL}/", "token": "  ", "verify_ssl": False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {"url": NEW_URL, "token": TOKEN, "verify_ssl": False}
    assert config_entry.unique_id == NEW_URL
    assert aioclient_mock.mock_calls[1][3]["Authorization"] == f"Bearer {TOKEN}"


async def test_reconfigure_same_address_new_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The same address with a new token is fine."""
    config_entry.add_to_hass(hass)
    healthy(aioclient_mock)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "token": "qm_rotated", "verify_ssl": True}
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data["token"] == "qm_rotated"
    assert config_entry.unique_id == URL


async def test_reconfigure_to_other_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Pointing an entry at a server another entry already uses is refused."""
    config_entry.add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, unique_id=NEW_URL, data={"url": NEW_URL, "token": TOKEN}).add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": NEW_URL.upper(), "verify_ssl": True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert aioclient_mock.call_count == 0
