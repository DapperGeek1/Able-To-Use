"""Set up the Able to Use integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AbleClient
from .const import CONF_ACCOUNT_ID, CONF_PROJECT_KEY, CONF_REFRESH_TOKEN
from .coordinator import AbleConfigEntry, AbleCoordinator

PLATFORMS = [Platform.COVER, Platform.BUTTON, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: AbleConfigEntry) -> bool:
    """Load one Able account and forward its entity platforms."""

    @callback
    def save_refresh_token(refresh_token: str) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN: refresh_token})

    client = AbleClient(
        async_get_clientsession(hass), entry.data[CONF_PROJECT_KEY], entry.data[CONF_ACCOUNT_ID],
        entry.data[CONF_REFRESH_TOKEN], save_refresh_token,
    )
    coordinator = AbleCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AbleConfigEntry) -> bool:
    """Unload the account and its platform entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
