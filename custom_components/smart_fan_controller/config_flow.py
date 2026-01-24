import logging
import voluptuous as vol

from homeassistant import config_entries
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

class SmartFanControllerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for for Smart Fan Controller."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        _errors = {}

        if user_input is not None:
            return self.async_create_entry(title=f"{user_input[CONF_CLIMATE_ENTITY]}", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN)
                ),
                vol.Optional(CONF_DEADBAND, default=DEFAULT_DEADBAND): float,
                vol.Optional(CONF_MIN_INTERVAL, default=DEFAULT_MIN_INTERVAL): int,
                vol.Optional(CONF_SOFT_ERROR, default=DEFAULT_SOFT_ERROR): float,
                vol.Optional(CONF_HARD_ERROR, default=DEFAULT_HARD_ERROR): float,
                vol.Optional(CONF_TEMPERATURE_PROJECTED_ERROR, default=DEFAULT_TEMPERATURE_PROJECTED_ERROR): float,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=_errors
        )

    async def async_step_init(self, user_input=None):
        """Handle options flow (when user modifies settings)."""
        if user_input is not None:
            # Update the entry data with new values
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=user_input
            )
            # Reload the entry to apply changes
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_abort(reason="reconfigure_successful")

        current_entry = self.hass.config_entries.async_get_entry(self.config_entry.entry_id)
        current_data = current_entry.data if current_entry else {}

        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE_ENTITY, default=current_data.get(CONF_CLIMATE_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN)
                ),
                vol.Optional(CONF_DEADBAND, default=current_data.get(CONF_DEADBAND, DEFAULT_DEADBAND)): float,
                vol.Optional(CONF_MIN_INTERVAL, default=current_data.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL)): int,
                vol.Optional(CONF_SOFT_ERROR, default=current_data.get(CONF_SOFT_ERROR, DEFAULT_SOFT_ERROR)): float,
                vol.Optional(CONF_HARD_ERROR, default=current_data.get(CONF_HARD_ERROR, DEFAULT_HARD_ERROR)): float,
                vol.Optional(CONF_TEMPERATURE_PROJECTED_ERROR, default=current_data.get(CONF_TEMPERATURE_PROJECTED_ERROR, DEFAULT_TEMPERATURE_PROJECTED_ERROR)): float,
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=data_schema
        )
