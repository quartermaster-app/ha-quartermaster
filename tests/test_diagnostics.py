"""Diagnostics."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.quartermaster.api import QuartermasterConnectionError

from .conftest import HOUSEHOLD, TOKEN, FakeStream, setup_entry


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
) -> None:
    """Diagnostics help without leaking the token, address, household or items."""
    await setup_entry(hass, config_entry)
    fake_stream.fail(QuartermasterConnectionError("Cannot connect to host qm.test:80"))
    await hass.async_block_till_done()

    diag = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    text = str(diag)
    for secret in (TOKEN, "qm.test", HOUSEHOLD, "Eggs", "Bread", "2 dozen"):
        assert secret not in text
    assert diag["entry"]["data"] == {"url": "**REDACTED**", "token": "**REDACTED**", "verify_ssl": True}
    assert diag["entry"]["title"] == "**REDACTED**"
    assert diag["server"] == {
        "version": "0.1.0",
        "protocol": 1,
        "ha_api": 1,
        "reports_server_id": True,
        "entry_uses_server_id": True,
        "household_named": True,
    }
    assert "0192a3b4" not in text
    assert diag["items"] == {"total": 2, "needs_action": 1, "completed": 1, "with_description": 1}
    assert diag["presence"] == {"members": 2, "shopping": 1}
    assert diag["coordinator"]["polling_interval"] == 60
    assert diag["stream"]["connected"] is False
    assert diag["stream"]["last_error"] == "Cannot connect to host **REDACTED**:80"
    assert diag["attribution"] == {"wrapped_intents": []}
    assert diag["repair_issues"] == []


async def test_diagnostics_connected(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    fake_stream: FakeStream,
) -> None:
    """While connected there's no polling interval and no error."""
    await setup_entry(hass, config_entry)
    fake_stream.push("changed", {"seq": 1})
    await hass.async_block_till_done()
    diag = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    assert diag["coordinator"]["polling_interval"] is None
    assert diag["stream"]["connected"] is True
    assert diag["stream"]["last_error"] is None
