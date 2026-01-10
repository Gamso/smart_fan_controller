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
