import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN

from .const import (
    DOMAIN,
    CONF_CLIMATE_ENTITY,
    CONF_DEADBAND,
    CONF_MIN_INTERVAL,
    CONF_SOFT_ERROR,
    CONF_HARD_ERROR,
    CONF_TEMPERATURE_PROJECTED_ERROR,
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SOFT_ERROR,
    DEFAULT_HARD_ERROR,
    DEFAULT_TEMPERATURE_PROJECTED_ERROR
)

_LOGGER = logging.getLogger(__name__)


def _get_climates_with_fan_modes(hass) -> list[str]:
    """Return climate entity_ids that expose fan_modes."""
    return [
        state.entity_id
        for state in hass.states.async_all(CLIMATE_DOMAIN)
        if state.attributes.get("fan_modes")
    ]


class SmartFanControllerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for for Smart Fan Controller."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        available_climates = _get_climates_with_fan_modes(self.hass)

        if user_input is not None:
            climate_id = user_input[CONF_CLIMATE_ENTITY]
            state = self.hass.states.get(climate_id)
            if not state or not state.attributes.get("fan_modes"):
                errors[CONF_CLIMATE_ENTITY] = "no_fan_modes"
            else:
                return self.async_create_entry(
                    title=f"{user_input[CONF_CLIMATE_ENTITY]}",
                    data=user_input
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=CLIMATE_DOMAIN,
                        include_entities=available_climates or None,
                    )
                ),
                vol.Optional(CONF_DEADBAND, default=DEFAULT_DEADBAND): float,
                vol.Optional(CONF_MIN_INTERVAL, default=DEFAULT_MIN_INTERVAL): int,
                vol.Optional(CONF_SOFT_ERROR, default=DEFAULT_SOFT_ERROR): float,
                vol.Optional(CONF_HARD_ERROR, default=DEFAULT_HARD_ERROR): float,
                vol.Optional(CONF_TEMPERATURE_PROJECTED_ERROR, default=DEFAULT_TEMPERATURE_PROJECTED_ERROR): float,
            }
        )

        if not available_climates:
            errors["base"] = "no_climate_with_fan_modes"

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return SmartFanControllerOptionsFlow(config_entry)


class SmartFanControllerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Smart Fan Controller."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        available_climates = _get_climates_with_fan_modes(self.hass)
        errors: dict[str, str] = {}

        if user_input is not None:
            climate_id = user_input[CONF_CLIMATE_ENTITY]
            state = self.hass.states.get(climate_id)
            if not state or not state.attributes.get("fan_modes"):
                errors[CONF_CLIMATE_ENTITY] = "no_fan_modes"
            else:
                return self.async_create_entry(title="", data=user_input)

        current_data = self.config_entry.data

        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_CLIMATE_ENTITY,
                    default=current_data.get(CONF_CLIMATE_ENTITY)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=CLIMATE_DOMAIN,
                        include_entities=available_climates or None,
                    )
                ),
                vol.Optional(
                    CONF_DEADBAND,
                    default=current_data.get(CONF_DEADBAND, DEFAULT_DEADBAND)
                ): float,
                vol.Optional(
                    CONF_MIN_INTERVAL,
                    default=current_data.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL)
                ): int,
                vol.Optional(
                    CONF_SOFT_ERROR,
                    default=current_data.get(CONF_SOFT_ERROR, DEFAULT_SOFT_ERROR)
                ): float,
                vol.Optional(
                    CONF_HARD_ERROR,
                    default=current_data.get(CONF_HARD_ERROR, DEFAULT_HARD_ERROR)
                ): float,
                vol.Optional(
                    CONF_TEMPERATURE_PROJECTED_ERROR,
                    default=current_data.get(
                        CONF_TEMPERATURE_PROJECTED_ERROR,
                        DEFAULT_TEMPERATURE_PROJECTED_ERROR
                    )
                ): float,
            }
        )

        if not available_climates:
            errors["base"] = "no_climate_with_fan_modes"

        return self.async_show_form(step_id="init", data_schema=options_schema, errors=errors)
