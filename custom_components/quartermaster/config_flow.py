"""Config flow for Quartermaster."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import probatio as vol

from .api import (
    QuartermasterAuthError,
    QuartermasterClient,
    QuartermasterError,
    QuartermasterInvalidResponseError,
    ServerStatus,
    normalize_url,
)
from .const import DOMAIN, LOGGER
from .coordinator import device_name

URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): URL_SELECTOR,
        vol.Required(CONF_TOKEN): TOKEN_SELECTOR,
        vol.Required(CONF_VERIFY_SSL, default=True): BooleanSelector(),
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR})
RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): URL_SELECTOR,
        vol.Optional(CONF_TOKEN): TOKEN_SELECTOR,
        vol.Required(CONF_VERIFY_SSL): BooleanSelector(),
    }
)


def unique_id_for(url: str) -> str:
    """Servers are identified by their address."""
    return normalize_url(url).lower()


class QuartermasterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Connect to a Quartermaster server with a Home Assistant token."""

    VERSION = 1

    async def _async_validate(
        self, url: str, token: str, verify_ssl: bool
    ) -> tuple[dict[str, str], ServerStatus | None]:
        """Check the server and the token. Returns (errors, server status)."""
        if not url.startswith(("http://", "https://")):
            return {CONF_URL: "invalid_url"}, None
        client = QuartermasterClient(async_get_clientsession(self.hass, verify_ssl=verify_ssl), url, token)
        try:
            status = await client.async_get_status()
            if status.setup_required:
                return {"base": "setup_required"}, None
            await client.async_get_items()
        except QuartermasterAuthError:
            return {"base": "invalid_auth"}, None
        except QuartermasterInvalidResponseError as err:
            LOGGER.debug("%s doesn't look like Quartermaster: %s", url, err)
            return {"base": "not_quartermaster"}, None
        except QuartermasterError as err:
            LOGGER.debug("Cannot reach Quartermaster at %s: %s", url, err)
            return {"base": "cannot_connect"}, None
        except Exception:  # noqa: BLE001  # shown to the user as "unknown"
            LOGGER.exception("Unexpected error checking Quartermaster")
            return {"base": "unknown"}, None
        return {}, status

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the server address and a token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_TOKEN].strip()
            verify_ssl = user_input[CONF_VERIFY_SSL]
            await self.async_set_unique_id(unique_id_for(url))
            self._abort_if_unique_id_configured()
            errors, status = await self._async_validate(url, token, verify_ssl)
            if status is not None:
                return self.async_create_entry(
                    title=device_name(status),
                    data={CONF_URL: url, CONF_TOKEN: token, CONF_VERIFY_SSL: verify_ssl},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication when the token stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            errors, _ = await self._async_validate(entry.data[CONF_URL], token, entry.data.get(CONF_VERIFY_SSL, True))
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"url": entry.data[CONF_URL]},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the server address, the token, or certificate checking."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = (user_input.get(CONF_TOKEN) or "").strip() or entry.data[CONF_TOKEN]
            verify_ssl = user_input[CONF_VERIFY_SSL]
            unique_id = unique_id_for(url)
            if unique_id != entry.unique_id and any(
                other.unique_id == unique_id
                for other in self._async_current_entries()
                if other.entry_id != entry.entry_id
            ):
                return self.async_abort(reason="already_configured")
            errors, _ = await self._async_validate(url, token, verify_ssl)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    data_updates={CONF_URL: url, CONF_TOKEN: token, CONF_VERIFY_SSL: verify_ssl},
                )
        suggested = {
            CONF_URL: entry.data[CONF_URL],
            CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, True),
            **(user_input or {}),
        }
        suggested.pop(CONF_TOKEN, None)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(RECONFIGURE_SCHEMA, suggested),
            errors=errors,
        )
