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
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SOFT_ERROR,
    DEFAULT_HARD_ERROR,
    DEFAULT_TEMPERATURE_PROJECTED_ERROR,
    DELTA_TIME_CONTROL_LOOP
)
from .controller import SmartFanController

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

async def async_setup_entry(hass, entry):
    """Set up the integration from a config entry."""
    # 1. Retrieve settings from config entry
    conf = entry.data
    climate_id = conf[CONF_CLIMATE_ENTITY]

    # 2. Instantiate the controller with dynamic parameters from Config Flow
    controller = SmartFanController(
        fan_modes=None,
        deadband=conf.get(CONF_DEADBAND, DEFAULT_DEADBAND),
        min_interval=conf.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
        soft_error=conf.get(CONF_SOFT_ERROR, DEFAULT_SOFT_ERROR),
        hard_error=conf.get(CONF_HARD_ERROR, DEFAULT_HARD_ERROR),
        projected_error_threshold=conf.get(CONF_TEMPERATURE_PROJECTED_ERROR, DEFAULT_TEMPERATURE_PROJECTED_ERROR)
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
        if controller._fan_modes is None:
            raw_modes = current_state.attributes.get("fan_modes", [])
            # Remove "auto" from the list of modes
            controller._fan_modes = [m for m in raw_modes if m.lower() not in ["auto", "off"]]
            _LOGGER.info("Detected fan modes for %s: %s", climate_id, controller._fan_modes)

        # Extract VTherm and Climate data
        attrs = current_state.attributes
        vtherm_slope = attrs.get("specific_states", {}).get("temperature_slope", 0)
        current_temp = attrs.get("current_temperature")
        target_temp = attrs.get("temperature")
        hvac_mode = attrs.get("hvac_mode")
        current_fan = attrs.get("fan_mode")

        if current_temp is None or target_temp is None:
            _LOGGER.debug("Incomplete data for %s, skipping cycle", climate_id)
            return

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
                sensor.update_from_controller(decision)

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
                sensor.update_from_controller(manual_data)

    # Schedule the loop and run it immediately once to initialize
    remove_timer = async_track_time_interval(hass, run_control_loop, timedelta(minutes=DELTA_TIME_CONTROL_LOOP))
    manual_change = async_track_state_change_event(hass, [climate_id], _handle_manual_change)
    # This ensures the timer stops if the integration is unloaded/removed
    entry.async_on_unload(remove_timer)
    entry.async_on_unload(manual_change)

    # Trigger first run immediately after setup
    hass.async_create_task(run_control_loop(None))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry when it's being removed."""
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, Platform.SENSOR)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)