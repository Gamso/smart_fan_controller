"""Config and options flow for Smart Fan Controller."""
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN

from .const import (
    DOMAIN,
    CONF_CLIMATE_ENTITY,
    CONF_FAN_MODE_ORDER,
    CONF_DEADBAND,
    CONF_MIN_INTERVAL,
    CONF_DATA_COLLECTION,
    CONF_DEFROST_ENTITY,
    CONF_OPERATING_ENTITY,
    CONF_OUTDOOR_ENTITY,
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_DATA_COLLECTION,
)


def _get_climates_with_fan_modes_and_slope(hass) -> list[str]:
    """Return climate entity_ids that expose fan_modes and temperature_slope (VTherm)."""
    return [
        state.entity_id
        for state in hass.states.async_all(CLIMATE_DOMAIN)
        if state.attributes.get("fan_modes") and state.attributes.get("specific_states", {}).get("temperature_slope") is not None
    ]


def _validate_climate_state(state) -> str | None:
    """Return an error key if the climate state is not a valid VTherm entity, else None."""
    if not state or not state.attributes.get("fan_modes"):
        return "invalid_climate_entity"
    if state.attributes.get("specific_states", {}).get("temperature_slope") is None:
        return "invalid_climate_entity"
    return None


def _extract_fan_modes(state) -> list[str]:
    """Return the manual fan modes exposed by a climate state (excludes auto/off)."""
    if state is None:
        return []
    raw_modes = state.attributes.get("fan_modes")
    if not raw_modes:
        return []
    return [mode for mode in raw_modes if isinstance(mode, str) and mode.lower() not in {"auto", "off"}]


def _validate_fan_order(selected: list[str] | None, detected: list[str]) -> str | None:
    """Return an error key when the submitted fan order is unusable, else None.

    An empty selection means "keep the climate entity's own order" and is valid.
    A partial selection is rejected: a half-specified ladder is worse than none,
    since the unlisted modes would be silently appended in detected order and
    the user would believe they had set the strength order explicitly.
    """
    if not selected:
        return None
    if set(selected) != set(detected):
        return "fan_order_incomplete"
    return None


def _is_climate_entity_already_configured(
    hass,
    climate_entity: str,
    *,
    exclude_entry_id: str | None = None,
) -> bool:
    """Return True when another Smart Fan Controller entry already targets the climate."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if exclude_entry_id is not None and entry.entry_id == exclude_entry_id:
            continue

        existing_conf = {**entry.data, **entry.options}
        if existing_conf.get(CONF_CLIMATE_ENTITY) == climate_entity:
            return True

    return False


class SmartFanControllerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for for Smart Fan Controller."""

    VERSION = 1

    def is_matching(self, other_flow: config_entries.ConfigFlow) -> bool:
        """Return False — multiple entries (one per climate entity) are allowed."""
        return False

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
            climate_error = _validate_climate_state(state)
            if climate_error:
                errors[CONF_CLIMATE_ENTITY] = climate_error
            elif _is_climate_entity_already_configured(self.hass, climate_id):
                errors[CONF_CLIMATE_ENTITY] = "already_configured"
            else:
                return self.async_create_entry(title=user_input[CONF_CLIMATE_ENTITY], data=user_input)

        # Build selector config without include_entities when none are available
        selector_config_kwargs: dict[str, Any] = {"domain": CLIMATE_DOMAIN}
        if available_climates:
            selector_config_kwargs["include_entities"] = available_climates

        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(**selector_config_kwargs)),
                vol.Optional(CONF_DEADBAND, default=DEFAULT_DEADBAND): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=5.0, step=0.05, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C")
                ),
                vol.Optional(CONF_MIN_INTERVAL, default=DEFAULT_MIN_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")
                ),
                vol.Optional(CONF_DATA_COLLECTION, default=DEFAULT_DATA_COLLECTION): selector.BooleanSelector(),
                vol.Optional(CONF_DEFROST_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain=["binary_sensor", "sensor", "input_boolean"])),
                vol.Optional(CONF_OPERATING_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain=["binary_sensor", "sensor", "input_boolean"])),
                vol.Optional(CONF_OUTDOOR_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="temperature")),
            }
        )

        if not available_climates:
            errors["base"] = "no_versatile_thermostat"

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )


class SmartFanControllerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Smart Fan Controller."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        available_climates = _get_climates_with_fan_modes_and_slope(self.hass)
        errors: dict[str, str] = {}
        current_data = {**self.config_entry.data, **self.config_entry.options}
        current_climate = current_data.get(CONF_CLIMATE_ENTITY)
        detected_fan_modes = _extract_fan_modes(self.hass.states.get(current_climate)) if current_climate else []

        if user_input is not None:
            climate_id = user_input[CONF_CLIMATE_ENTITY]
            current_climate_id = current_data.get(CONF_CLIMATE_ENTITY)

            order_error = _validate_fan_order(user_input.get(CONF_FAN_MODE_ORDER), detected_fan_modes)
            if order_error:
                errors[CONF_FAN_MODE_ORDER] = order_error

            # Only validate the climate entity if the user is changing it
            if climate_id != current_climate_id:
                state = self.hass.states.get(climate_id)
                climate_error = _validate_climate_state(state)
                if climate_error:
                    errors[CONF_CLIMATE_ENTITY] = climate_error
                elif _is_climate_entity_already_configured(
                    self.hass,
                    climate_id,
                    exclude_entry_id=self.config_entry.entry_id,
                ):
                    errors[CONF_CLIMATE_ENTITY] = "already_configured"

            if not errors:
                return self.async_create_entry(title="", data=user_input)

        # Build selector config for options flow
        # Always include the current climate entity even if it's not in the filtered list
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
                required_key: selector.EntitySelector(selector.EntitySelectorConfig(**selector_config_kwargs)),
                vol.Optional(CONF_DEADBAND, default=current_data.get(CONF_DEADBAND, DEFAULT_DEADBAND)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=5.0, step=0.05, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C")
                ),
                vol.Optional(CONF_MIN_INTERVAL, default=current_data.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")
                ),
                vol.Optional(CONF_DATA_COLLECTION, default=current_data.get(CONF_DATA_COLLECTION, DEFAULT_DATA_COLLECTION)): selector.BooleanSelector(),
                vol.Optional(CONF_DEFROST_ENTITY, default=current_data.get(CONF_DEFROST_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "sensor", "input_boolean"])
                ),
                vol.Optional(CONF_OPERATING_ENTITY, default=current_data.get(CONF_OPERATING_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "sensor", "input_boolean"])
                ),
                vol.Optional(CONF_OUTDOOR_ENTITY, default=current_data.get(CONF_OUTDOOR_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                # Pick every speed, weakest first. Left empty, the climate entity's
                # own fan_modes order is used (see _apply_configured_fan_order).
                vol.Optional(
                    CONF_FAN_MODE_ORDER,
                    default=current_data.get(CONF_FAN_MODE_ORDER, detected_fan_modes),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=detected_fan_modes,
                        multiple=True,
                        sort=False,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema, errors=errors)
