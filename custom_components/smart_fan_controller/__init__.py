"""Initialisation of Smart Fan Controller."""
import logging
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.const import Platform

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
    DEFAULT_LIMIT_TIMEOUT,
    DELTA_TIME_CONTROL_LOOP
)
from .controller import SmartFanController

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    # 1. Retrieve settings from config entry (options override data)
    conf = {**entry.data, **entry.options}
    climate_id = conf[CONF_CLIMATE_ENTITY]

    # Restore learning data if available
    learning_data = entry.data.get("learning_data")

    # 2. Instantiate the controller with dynamic parameters from Config Flow
    controller = SmartFanController(
        fan_modes=None,
        deadband=conf.get(CONF_DEADBAND, DEFAULT_DEADBAND),
        min_interval=conf.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
        soft_error=conf.get(CONF_SOFT_ERROR, DEFAULT_SOFT_ERROR),
        hard_error=conf.get(CONF_HARD_ERROR, DEFAULT_HARD_ERROR),
        projected_error_threshold=conf.get(CONF_TEMPERATURE_PROJECTED_ERROR, DEFAULT_TEMPERATURE_PROJECTED_ERROR),
        limit_timeout=conf.get(CONF_LIMIT_TIMEOUT, DEFAULT_LIMIT_TIMEOUT),
        learning_data=learning_data
    )

    # 3. Store data for platforms and forward setup
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "controller": controller,
        "climate_entity": climate_id,
        "sensor": None # Reference will be set in sensor.py
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def run_control_loop(_):
        """Main control loop executed every 2 minutes."""
        current_state = hass.states.get(climate_id)

        # Guard clause if the climate entity is still missing from the state machine
        if not current_state:
            _LOGGER.warning("Climate entity %s not found", climate_id)
            return

        # Dynamically fetch fan modes if the controller doesn't have them yet
        if controller.fan_modes is None:
            raw_modes = current_state.attributes.get("fan_modes", [])
            # Remove "auto" from the list of modes
            controller.fan_modes = [m for m in raw_modes if m.lower() not in ["auto", "off"]]
            _LOGGER.info("Detected fan modes for %s: %s", climate_id, controller.fan_modes)

        # Extract VTherm and Climate data
        attrs = current_state.attributes
        vtherm_slope = attrs.get("specific_states", {}).get("temperature_slope", 0)
        current_temp = attrs.get("current_temperature")
        target_temp = attrs.get("temperature")
        hvac_mode = attrs.get("hvac_mode")
        current_fan = attrs.get("fan_mode")

        if vtherm_slope is None:
            _LOGGER.warning("%s missing VTherm temperature_slope; skipping control cycle", climate_id)
            return

        if current_temp is None or target_temp is None:
            _LOGGER.debug("Incomplete temperature data for %s, skipping cycle", climate_id)
            return

        _LOGGER.debug(
            "Cycle: temp=%.2f target=%.2f slope=%.3f fan=%s hvac=%s",
            current_temp,
            target_temp,
            vtherm_slope,
            current_fan,
            hvac_mode,
        )

        # Execute decision logic
        decision = controller.calculate_decision(
            float(current_temp),
            float(target_temp),
            float(vtherm_slope),
            str(hvac_mode),
            current_fan
        )

        # Update all sensors stored in the list
        sensors = hass.data[DOMAIN][entry.entry_id].get("sensors")
        if sensors:
            _LOGGER.debug("Updating %s diagnostic sensors", len(sensors))
            for sensor in sensors:
                if hasattr(sensor, 'update_from_controller'):
                    sensor.update_from_controller(decision)
                else:
                    # Learning sensor updates itself via properties
                    sensor.async_write_ha_state()

        # Apply the new fan speed if a change is required
        if decision["fan_mode"] != current_fan:
            _LOGGER.info(
                "Changing %s fan to %s. Reason: %s",
                climate_id, decision["fan_mode"], decision["reason"]
            )
            await hass.services.async_call("climate", "set_fan_mode", {
                "entity_id": climate_id,
                "fan_mode": decision["fan_mode"]
            })

    async def _handle_manual_change(event):
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        new_fan = new_state.attributes.get("fan_mode")
        old_fan = old_state.attributes.get("fan_mode")

        if new_fan != old_fan:
            _LOGGER.info("Manual fan_mode change detected, resetting timer")

            # Reset internal controller timer
            manual_data = controller.update_new_fan_state(new_fan)

            # Instantly refresh sensors to show the change
            sensors = hass.data[DOMAIN][entry.entry_id].get("sensors", [])
            for sensor in sensors:
                if hasattr(sensor, "update_from_controller"):
                    sensor.update_from_controller(manual_data)
                else:
                    # Learning sensor updates itself via properties
                    sensor.async_write_ha_state()

    # Schedule the loop and run it immediately once to initialize
    remove_timer = async_track_time_interval(hass, run_control_loop, timedelta(minutes=DELTA_TIME_CONTROL_LOOP))
    manual_change = async_track_state_change_event(hass, [climate_id], _handle_manual_change)
    # This ensures the timer stops if the integration is unloaded/removed
    entry.async_on_unload(remove_timer)
    entry.async_on_unload(manual_change)

    # Trigger first run immediately after setup
    hass.async_create_task(run_control_loop(None))

    # Register service to apply learned parameters
    async def apply_learned_settings(_):
        """Service to apply optimal parameters from learning."""
        if not controller.learning.is_ready():
            _LOGGER.warning("Learning not complete yet (%.1f%%), cannot apply settings", controller.learning.get_progress())
            return

        optimal = controller.learning.compute_optimal_parameters()
        if not optimal:
            _LOGGER.error("Failed to compute optimal parameters")
            return

        # Update config entry with new values
        new_data = {**entry.data}
        new_data[CONF_DEADBAND] = optimal["deadband"]
        new_data[CONF_SOFT_ERROR] = optimal["soft_error"]
        new_data[CONF_HARD_ERROR] = optimal["hard_error"]
        new_data[CONF_TEMPERATURE_PROJECTED_ERROR] = optimal["projected_error_threshold"]
        new_data[CONF_LIMIT_TIMEOUT] = optimal["limit_timeout"]

        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.info(
            "Applied learned settings: deadband=%.2f soft_error=%.2f hard_error=%.2f limit_timeout=%d",
            optimal["deadband"],
            optimal["soft_error"],
            optimal["hard_error"],
            optimal["limit_timeout"],
        )

        # Reload to apply new parameters
        await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(DOMAIN, "apply_learned_settings", apply_learned_settings)

    # Register service to reset learning data
    async def reset_learning(_):
        """Service to clear all learning data and restart learning."""
        controller.learning.reset()

        # Persist cleared data to config entry to avoid reloading old stats on restart
        new_data = {**entry.data, "learning_data": controller.learning.to_dict()}
        hass.config_entries.async_update_entry(entry, data=new_data)

        # Refresh sensors immediately
        sensors = hass.data[DOMAIN][entry.entry_id].get("sensors", [])
        for sensor in sensors:
            sensor.async_write_ha_state()

        _LOGGER.info("Learning reset: all samples and stats cleared")

    hass.services.async_register(DOMAIN, "reset_learning", reset_learning)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry when it's being removed."""
    # Save learning data before unloading
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        controller = entry_data.get("controller")
        if controller and hasattr(controller, 'learning'):
            learning_data = controller.learning.to_dict()
            # Store in config entry
            new_data = {**entry.data, "learning_data": learning_data}
            hass.config_entries.async_update_entry(entry, data=new_data)

    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, Platform.SENSOR)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
