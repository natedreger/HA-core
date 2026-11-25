"""Speaker group and source selection for forked-daapd."""

from typing import Any, override

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    FD_NAME,
    SIGNAL_CONFIG_OPTIONS_UPDATE,
    SIGNAL_UPDATE_OUTPUTS,
    SIGNAL_UPDATE_SOURCE,
    SPEAKER_GROUP_MODIFIED_SUFFIX,
)
from .coordinator import ForkedDaapdConfigEntry, ForkedDaapdUpdater


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ForkedDaapdConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects from a config entry."""
    async_add_entities(
        [
            ForkedDaapdSpeakerGroupSelect(config_entry.runtime_data, config_entry),
            ForkedDaapdSourceSelect(config_entry.runtime_data, config_entry),
        ]
    )


class ForkedDaapdSpeakerGroupSelect(SelectEntity):
    """Select which speaker group is enabled."""

    _attr_icon = "mdi:speaker-multiple"
    _attr_name = f"{FD_NAME} speaker group"
    _attr_should_poll = False

    def __init__(
        self, updater: ForkedDaapdUpdater, config_entry: ForkedDaapdConfigEntry
    ) -> None:
        """Initialize the speaker group select."""
        self._updater = updater
        self._entry_id = config_entry.entry_id
        self._attr_device_info = updater.device_info
        self._attr_unique_id = f"{config_entry.entry_id}-speaker_group"

    @override
    async def async_added_to_hass(self) -> None:
        """Use lifecycle hooks."""
        for signal in (SIGNAL_UPDATE_OUTPUTS, SIGNAL_CONFIG_OPTIONS_UPDATE):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    signal.format(self._entry_id),
                    self._async_handle_update,
                )
            )

    @callback
    def _async_handle_update(self, *args: Any) -> None:
        """Write the new state."""
        self.async_write_ha_state()

    @property
    @override
    def available(self) -> bool:
        """Return whether the server has reported its outputs."""
        return bool(self._updater.outputs)

    @property
    @override
    def options(self) -> list[str]:
        """Return the speaker groups, plus the current one if it was modified."""
        options = list(self._updater.speaker_groups)
        # The current option must be listed or the state becomes unknown
        if (current := self.current_option) and current not in options:
            options.append(current)
        return options

    @property
    @override
    def current_option(self) -> str | None:
        """Return the group matching the current outputs."""
        return self._updater.current_speaker_group

    @override
    async def async_select_option(self, option: str) -> None:
        """Apply the selected group, restoring its preset volumes."""
        if option not in self._updater.speaker_groups:
            option = option.removesuffix(SPEAKER_GROUP_MODIFIED_SUFFIX)
        await self._updater.async_apply_speaker_group(option)


class ForkedDaapdSourceSelect(SelectEntity):
    """Select the source of the server."""

    _attr_icon = "mdi:playlist-music"
    _attr_name = f"{FD_NAME} source"
    _attr_should_poll = False

    def __init__(
        self, updater: ForkedDaapdUpdater, config_entry: ForkedDaapdConfigEntry
    ) -> None:
        """Initialize the source select."""
        self._updater = updater
        self._entry_id = config_entry.entry_id
        self._attr_device_info = updater.device_info
        self._attr_unique_id = f"{config_entry.entry_id}-source"

    @override
    async def async_added_to_hass(self) -> None:
        """Use lifecycle hooks."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE_SOURCE.format(self._entry_id),
                self._async_handle_update,
            )
        )

    @callback
    def _async_handle_update(self) -> None:
        """Write the new state."""
        self.async_write_ha_state()

    @property
    @override
    def available(self) -> bool:
        """Return whether the server has reported its sources."""
        master = self._updater.master
        return master is not None and master.available and bool(master.source_list)

    @property
    @override
    def options(self) -> list[str]:
        """Return the sources."""
        if (master := self._updater.master) is None:
            return []
        return master.source_list

    @property
    @override
    def current_option(self) -> str | None:
        """Return the current source."""
        if (master := self._updater.master) is None:
            return None
        return master.source

    @override
    async def async_select_option(self, option: str) -> None:
        """Change the source, starting playback of playlists."""
        if (master := self._updater.master) is not None:
            await master.async_select_source_and_play(option)
