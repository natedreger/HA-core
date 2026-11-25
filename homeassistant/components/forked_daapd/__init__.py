"""The forked_daapd component."""

import probatio
from pyforked_daapd import ForkedDaapdAPI

from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerEntityFeature,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import ForkedDaapdConfigEntry, ForkedDaapdUpdater

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SELECT]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "select_speaker_group",
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema={probatio.Required("speaker_group"): cv.string},
        func="async_select_speaker_group",
        # Only the server entity supports grouping
        required_features=[MediaPlayerEntityFeature.GROUPING],
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ForkedDaapdConfigEntry) -> None:
    """Set up forked-daapd from a config entry by forwarding to platform."""
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]
    password: str = entry.data[CONF_PASSWORD]
    forked_daapd_api = ForkedDaapdAPI(
        async_get_clientsession(hass), host, port, password
    )
    forked_daapd_updater = ForkedDaapdUpdater(hass, forked_daapd_api, entry.entry_id)
    entry.runtime_data = forked_daapd_updater
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(
    hass: HomeAssistant, entry: ForkedDaapdConfigEntry
) -> None:
    """Remove forked-daapd component."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    if websocket_handler := entry.runtime_data.websocket_handler:
        websocket_handler.cancel()
