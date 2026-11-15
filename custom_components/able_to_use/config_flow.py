"""Home Assistant setup, reauthentication, and polling options."""

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import AbleAuthError, AbleClient, AbleError, async_login
from .const import (
    CONF_ACCOUNT_ID, CONF_PROJECT_KEY, CONF_REFRESH_TOKEN, CONF_SCAN_INTERVAL,
    DEFAULT_PROJECT_KEY, DEFAULT_SCAN_INTERVAL, DOMAIN, MAX_SCAN_INTERVAL, MIN_SCAN_INTERVAL,
)

SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class AbleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Link an Able account with its existing login."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "AbleOptionsFlow":
        return AbleOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._sign_in("user", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._sign_in("reauth_confirm", user_input)

    async def _sign_in(self, step: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        entry = self._get_reauth_entry() if step == "reauth_confirm" else None
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            key = user_input.get(CONF_PROJECT_KEY, "").strip()
            if not key:
                key = (entry.data.get(CONF_PROJECT_KEY) if entry else None) or DEFAULT_PROJECT_KEY
            session = async_get_clientsession(self.hass)
            try:
                login = await async_login(session, key, email, user_input[CONF_PASSWORD])
                client = AbleClient(session, key, login.uid, login.refresh_token)
                client.use_login(login)
                await client.async_devices()
            except AbleAuthError:
                errors["base"] = "invalid_auth"
            except AbleError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(login.uid)
                data = {
                    CONF_EMAIL: email, CONF_ACCOUNT_ID: login.uid,
                    CONF_REFRESH_TOKEN: login.refresh_token,
                }
                if key != DEFAULT_PROJECT_KEY:
                    data[CONF_PROJECT_KEY] = key
                if entry:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(entry, data=data)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Able to Use", data=data)

        email_field = vol.Required(CONF_EMAIL, default=entry.data[CONF_EMAIL]) if entry else vol.Required(CONF_EMAIL)
        fields = {
            email_field: TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
            vol.Required(CONF_PASSWORD): SECRET,
            vol.Optional(CONF_PROJECT_KEY): SECRET,
        }
        return self.async_show_form(step_id=step, data_schema=vol.Schema(fields), errors=errors)


class AbleOptionsFlow(OptionsFlowWithReload):
    """Adjust cloud polling without changing the account."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
        }))
