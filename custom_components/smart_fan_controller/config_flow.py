from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
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
    CONF_LIMIT_TIMEOUT,
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SOFT_ERROR,
    DEFAULT_HARD_ERROR,
    DEFAULT_TEMPERATURE_PROJECTED_ERROR,
    DEFAULT_LIMIT_TIMEOUT
)


def _get_climates_with_fan_modes_and_slope(hass) -> list[str]:
    """Return climate entity_ids that expose fan_modes and temperature_slope (VTherm)."""
    return [
        state.entity_id
        for state in hass.states.async_all(CLIMATE_DOMAIN)
        if state.attributes.get("fan_modes") and state.attributes.get("specific_states", {}).get("temperature_slope") is not None
    ]


class SmartFanControllerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for for Smart Fan Controller."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return SmartFanControllerOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        available_climates = _get_climates_with_fan_modes_and_slope(self.hass)

        if user_input is not None:
            climate_id = user_input[CONF_CLIMATE_ENTITY]
            state = self.hass.states.get(climate_id)
            if not state or not state.attributes.get("fan_modes"):
                errors[CONF_CLIMATE_ENTITY] = "no_fan_modes"
            elif state.attributes.get("specific_states", {}).get("temperature_slope") is None:
                errors[CONF_CLIMATE_ENTITY] = "no_temperature_slope"
            else:
                return self.async_create_entry(
                    title=f"{user_input[CONF_CLIMATE_ENTITY]}",
                    data=user_input
                )

        # Build selector config without include_entities when none are available
        selector_config_kwargs: dict[str, Any] = {"domain": CLIMATE_DOMAIN}
        if available_climates:
            selector_config_kwargs["include_entities"] = available_climates

        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(**selector_config_kwargs)
                ),
                vol.Optional(CONF_DEADBAND, default=DEFAULT_DEADBAND): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=5.0, step=0.05, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_MIN_INTERVAL, default=DEFAULT_MIN_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=60, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_SOFT_ERROR, default=DEFAULT_SOFT_ERROR): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=10.0, step=0.05, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_HARD_ERROR, default=DEFAULT_HARD_ERROR): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=10.0, step=0.05, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_TEMPERATURE_PROJECTED_ERROR, default=DEFAULT_TEMPERATURE_PROJECTED_ERROR): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=10.0, step=0.05, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_LIMIT_TIMEOUT, default=DEFAULT_LIMIT_TIMEOUT): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=120, step=5, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )

        if not available_climates:
            errors["base"] = "no_climate_with_fan_modes"

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )


class SmartFanControllerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Smart Fan Controller."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        available_climates = _get_climates_with_fan_modes_and_slope(self.hass)
        errors: dict[str, str] = {}

        if user_input is not None:
            climate_id = user_input[CONF_CLIMATE_ENTITY]
            current_climate_id = self.config_entry.data.get(CONF_CLIMATE_ENTITY)

            # Only validate if user is changing the climate entity
            if climate_id != current_climate_id:
                state = self.hass.states.get(climate_id)
                if not state or not state.attributes.get("fan_modes"):
                    errors[CONF_CLIMATE_ENTITY] = "no_fan_modes"
                elif state.attributes.get("specific_states", {}).get("temperature_slope") is None:
                    errors[CONF_CLIMATE_ENTITY] = "no_temperature_slope"

            if not errors:
                return self.async_create_entry(title="", data=user_input)

        # Merge data and options (options take priority)
        current_data = {**self.config_entry.data, **self.config_entry.options}

        # Build selector config for options flow
        # Always include the current climate entity even if it's not in the filtered list
        current_climate = current_data.get(CONF_CLIMATE_ENTITY)
        selector_config_kwargs: dict[str, Any] = {"domain": CLIMATE_DOMAIN}
        if available_climates:
            selector_config_kwargs["include_entities"] = available_climates
        elif current_climate:
            # If no VTherm climates found but we have a configured one, allow editing
            selector_config_kwargs["include_entities"] = [current_climate]

        # Required key, only set default if present to avoid None default
        required_key = vol.Required(CONF_CLIMATE_ENTITY, default=current_data.get(CONF_CLIMATE_ENTITY)) if current_data.get(CONF_CLIMATE_ENTITY) is not None else vol.Required(CONF_CLIMATE_ENTITY)

        options_schema = vol.Schema(
            {
                required_key: selector.EntitySelector(
                    selector.EntitySelectorConfig(**selector_config_kwargs)
                ),
                vol.Optional(
                    CONF_DEADBAND,
                    default=current_data.get(CONF_DEADBAND, DEFAULT_DEADBAND)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=5.0, step=0.05, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_MIN_INTERVAL,
                    default=current_data.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=60, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_SOFT_ERROR,
                    default=current_data.get(CONF_SOFT_ERROR, DEFAULT_SOFT_ERROR)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=10.0, step=0.05, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_HARD_ERROR,
                    default=current_data.get(CONF_HARD_ERROR, DEFAULT_HARD_ERROR)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=10.0, step=0.05, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_TEMPERATURE_PROJECTED_ERROR,
                    default=current_data.get(
                        CONF_TEMPERATURE_PROJECTED_ERROR,
                        DEFAULT_TEMPERATURE_PROJECTED_ERROR
                    )
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=10.0, step=0.05, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_LIMIT_TIMEOUT,
                    default=current_data.get(CONF_LIMIT_TIMEOUT, DEFAULT_LIMIT_TIMEOUT)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=120, step=5, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema, errors=errors)
