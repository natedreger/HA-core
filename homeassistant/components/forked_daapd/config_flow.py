"""Config flow to configure forked-daapd devices."""

from contextlib import suppress
import logging
from typing import Any, override

import probatio
from pyforked_daapd import ForkedDaapdAPI

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_LIBRESPOT_JAVA_PORT,
    CONF_MAX_PLAYLISTS,
    CONF_SPEAKERS,
    CONF_TTS_PAUSE_TIME,
    CONF_TTS_VOLUME,
    DEFAULT_PORT,
    DEFAULT_TTS_PAUSE_TIME,
    DEFAULT_TTS_VOLUME,
    DOMAIN,
    SPEAKER_GROUP_NONE,
    SUBENTRY_TYPE_SPEAKER_GROUP,
    normalize_speaker_name,
)
from .coordinator import ForkedDaapdConfigEntry

_LOGGER = logging.getLogger(__name__)

DEFAULT_GROUP_VOLUME = 50


# Can't use all probatio types: https://github.com/home-assistant/core/issues/32819
DATA_SCHEMA_DICT = {
    probatio.Required(CONF_HOST): str,
    probatio.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    probatio.Optional(probatio.Secret(CONF_PASSWORD), default=""): str,
}

TEST_CONNECTION_ERROR_DICT = {
    "forbidden": "forbidden",
    "ok": "ok",
    "websocket_not_enabled": "websocket_not_enabled",
    "wrong_host_or_port": "wrong_host_or_port",
    "wrong_password": "wrong_password",
    "wrong_server_type": "wrong_server_type",
}


class ForkedDaapdOptionsFlowHandler(OptionsFlow):
    """Handle a forked-daapd options flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="options", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Optional(
                        CONF_TTS_PAUSE_TIME,
                        default=self.config_entry.options.get(
                            CONF_TTS_PAUSE_TIME, DEFAULT_TTS_PAUSE_TIME
                        ),
                    ): float,
                    probatio.Optional(
                        CONF_TTS_VOLUME,
                        default=self.config_entry.options.get(
                            CONF_TTS_VOLUME, DEFAULT_TTS_VOLUME
                        ),
                    ): float,
                    probatio.Optional(
                        CONF_LIBRESPOT_JAVA_PORT,
                        default=self.config_entry.options.get(
                            CONF_LIBRESPOT_JAVA_PORT, 24879
                        ),
                    ): int,
                    probatio.Optional(
                        CONF_MAX_PLAYLISTS,
                        default=self.config_entry.options.get(CONF_MAX_PLAYLISTS, 10),
                    ): int,
                }
            ),
        )


class SpeakerGroupSubentryFlowHandler(ConfigSubentryFlow):
    """Handle adding and editing a speaker group."""

    def __init__(self) -> None:
        """Initialize."""
        self._name = ""
        self._speakers: list[str] = []
        self._volumes: dict[str, int] = {}
        self._speaker_options: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a speaker group."""
        return await self._async_step_group("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a speaker group."""
        return await self._async_step_group("reconfigure", user_input)

    async def _async_step_group(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        """Ask for the group name and its speakers."""
        entry: ForkedDaapdConfigEntry = self._get_entry()
        subentry = (
            self._get_reconfigure_subentry() if step_id == "reconfigure" else None
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            self._name = user_input[CONF_NAME].strip()
            self._speakers = user_input[CONF_SPEAKERS]
            if not self._name:
                errors[CONF_NAME] = "name_required"
            elif self._name.casefold() == SPEAKER_GROUP_NONE.casefold() or any(
                other.subentry_type == SUBENTRY_TYPE_SPEAKER_GROUP
                and other.title.casefold() == self._name.casefold()
                and (subentry is None or other.subentry_id != subentry.subentry_id)
                for other in entry.subentries.values()
            ):
                errors[CONF_NAME] = "name_exists"
            elif self._speakers:
                return await self.async_step_volumes()
            else:
                return self._async_save()
        else:
            if entry.state is not ConfigEntryState.LOADED:
                return self.async_abort(reason="entry_not_loaded")
            if not (response := await entry.runtime_data.api.get_request("outputs")):
                return self.async_abort(reason="cannot_connect")
            names = {
                normalize_speaker_name(output["name"]): output["name"]
                for output in response["outputs"]
            }
            if subentry:
                self._name = subentry.title
                for speaker in subentry.data[CONF_SPEAKERS]:
                    # Keep saved speakers that are currently offline selectable
                    name = names.setdefault(
                        normalize_speaker_name(speaker["name"]), speaker["name"]
                    )
                    self._speakers.append(name)
                    self._volumes[name] = speaker["volume"]
            self._speaker_options = list(names.values())

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_NAME): str,
                        probatio.Required(CONF_SPEAKERS, default=[]): SelectSelector(
                            SelectSelectorConfig(
                                options=self._speaker_options, multiple=True
                            )
                        ),
                    }
                ),
                {CONF_NAME: self._name, CONF_SPEAKERS: self._speakers},
            ),
            errors=errors,
        )

    async def async_step_volumes(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for the preset volume of each speaker in the group."""
        if user_input is not None:
            self._volumes = {name: int(user_input[name]) for name in self._speakers}
            return self._async_save()

        volume_selector = NumberSelector(
            NumberSelectorConfig(
                min=0, max=100, step=1, mode=NumberSelectorMode.SLIDER
            )
        )
        return self.async_show_form(
            step_id="volumes",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        name,
                        default=self._volumes.get(name, DEFAULT_GROUP_VOLUME),
                    ): volume_selector
                    for name in self._speakers
                }
            ),
        )

    def _async_save(self) -> SubentryFlowResult:
        """Create or update the speaker group."""
        data = {
            CONF_SPEAKERS: [
                {CONF_NAME: name, "volume": self._volumes[name]}
                for name in self._speakers
            ]
        }
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=self._name,
                data=data,
            )
        return self.async_create_entry(title=self._name, data=data)


def fill_in_schema_dict(some_input):
    """Fill in schema dict defaults from user_input."""
    schema_dict = {}
    for field, _type in DATA_SCHEMA_DICT.items():
        if some_input.get(str(field)):
            schema_dict[
                probatio.Optional(str(field), default=some_input[str(field)])
            ] = _type
        else:
            schema_dict[field] = _type
    return schema_dict


class ForkedDaapdFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a forked-daapd config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self.discovery_schema: probatio.Schema | None = None

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: ForkedDaapdConfigEntry,
    ) -> ForkedDaapdOptionsFlowHandler:
        """Return options flow handler."""
        return ForkedDaapdOptionsFlowHandler()

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ForkedDaapdConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the supported subentry types."""
        return {SUBENTRY_TYPE_SPEAKER_GROUP: SpeakerGroupSubentryFlowHandler}

    async def validate_input(self, user_input):
        """Validate the user input."""
        websession = async_get_clientsession(self.hass)
        validate_result = await ForkedDaapdAPI.test_connection(
            websession=websession,
            host=user_input[CONF_HOST],
            port=user_input[CONF_PORT],
            password=user_input[CONF_PASSWORD],
        )
        validate_result[0] = TEST_CONNECTION_ERROR_DICT.get(
            validate_result[0], "unknown_error"
        )
        return validate_result

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a forked-daapd config flow start.

        Manage device specific parameters.
        """
        if user_input is not None:
            # check for any entries with same host, abort if found
            self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
            validate_result = await self.validate_input(user_input)
            if validate_result[0] == "ok":  # success
                _LOGGER.debug("Connected successfully. Creating entry")
                return self.async_create_entry(
                    title=validate_result[1], data=user_input
                )
            return self.async_show_form(
                step_id="user",
                data_schema=probatio.Schema(fill_in_schema_dict(user_input)),
                errors={"base": validate_result[0]},
            )
        if self.discovery_schema:  # stop at form to allow user to set up manually
            return self.async_show_form(
                step_id="user", data_schema=self.discovery_schema, errors={}
            )
        return self.async_show_form(
            step_id="user", data_schema=probatio.Schema(DATA_SCHEMA_DICT), errors={}
        )

    @override
    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Prepare configuration for a discovered forked-daapd device."""
        version_num = 0
        zeroconf_properties = discovery_info.properties
        if zeroconf_properties.get("Machine Name"):
            with suppress(ValueError):
                version_num = int(
                    zeroconf_properties.get("mtd-version", "0").split(".")[0]
                )
        if version_num < 27:
            return self.async_abort(reason="not_forked_daapd")
        await self.async_set_unique_id(zeroconf_properties["Machine Name"])
        self._abort_if_unique_id_configured()

        # Update title and abort if we already have an entry for this host
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) != discovery_info.host:
                continue
            self.hass.config_entries.async_update_entry(
                entry,
                title=zeroconf_properties["Machine Name"],
            )
            return self.async_abort(reason="already_configured")

        zeroconf_data = {
            CONF_HOST: discovery_info.host,
            CONF_PORT: discovery_info.port,
            CONF_NAME: zeroconf_properties["Machine Name"],
        }
        self.discovery_schema = probatio.Schema(fill_in_schema_dict(zeroconf_data))
        self.context.update({"title_placeholders": zeroconf_data})
        return await self.async_step_user()
