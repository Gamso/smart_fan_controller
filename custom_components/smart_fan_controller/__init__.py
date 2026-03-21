"""Initialisation of Smart Fan Controller."""
import logging
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.const import Platform

from .const import (
    DOMAIN,
    CONF_CLIMATE_ENTITY,
    CONF_DEADBAND,
    CONF_MIN_INTERVAL,
    CONF_SOFT_ERROR,
    CONF_HARD_ERROR,
    CONF_LIMIT_TIMEOUT,
    CONF_LEARNING_ENABLED,
    CONF_DATA_COLLECTION,
    CONF_MPC_SHADOW_ENABLED,
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SOFT_ERROR,
    DEFAULT_HARD_ERROR,
    DEFAULT_LIMIT_TIMEOUT,
    DEFAULT_LEARNING_ENABLED,
    DEFAULT_DATA_COLLECTION,
    DEFAULT_MPC_SHADOW_ENABLED,
    DELTA_TIME_CONTROL_LOOP,
    STORAGE_VERSION,
    STORAGE_KEY,
    LEARNING_DATA_SAVE_INTERVAL,
)
from .controller import SmartFanController
from .data_collection import DataCollector
from .mpc_shadow import MPCShadowController

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


async def _apply_optimal_parameters(
    hass: HomeAssistant,
    entry: ConfigEntry,
    optimal: dict,
) -> None:
    """Apply learned optimal parameters to the config entry and trigger a reload.

    This is a standalone helper factorised from both the auto-apply block and the
    apply_learned_settings service, so the logic lives in exactly one place.
    Sets the learning_auto_applied flag in entry.data so that a subsequent reload
    does not trigger a second auto-apply (breaking the infinite-reload loop).
    """
    new_data = {**entry.data}
    new_data["learning_auto_applied"] = True
    new_data[CONF_DEADBAND] = optimal["deadband"]
    new_data[CONF_SOFT_ERROR] = optimal["soft_error"]
    new_data[CONF_HARD_ERROR] = optimal["hard_error"]
    new_data[CONF_LIMIT_TIMEOUT] = optimal["limit_timeout"]

    _LOGGER.info(
        "Applying learned settings: deadband=%.2f soft_error=%.2f hard_error=%.2f limit_timeout=%d",
        optimal["deadband"],
        optimal["soft_error"],
        optimal["hard_error"],
        optimal["limit_timeout"],
    )

    hass.config_entries.async_update_entry(entry, data=new_data)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    # 1. Retrieve settings from config entry (options override data)
    conf = {**entry.data, **entry.options}
    climate_id = conf[CONF_CLIMATE_ENTITY]

    # Set up persistent storage for learning data
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")

    # Try to restore learning data from persistent storage first, then fall back to config entry
    learning_data = await store.async_load()
    if not learning_data:
        learning_data = entry.data.get("learning_data")
        _LOGGER.debug("No persistent storage found, using config entry data")

    # 2. Instantiate the controller with dynamic parameters from Config Flow
    controller = SmartFanController(
        fan_modes=None,
        deadband=conf.get(CONF_DEADBAND, DEFAULT_DEADBAND),
        min_interval=conf.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
        soft_error=conf.get(CONF_SOFT_ERROR, DEFAULT_SOFT_ERROR),
        hard_error=conf.get(CONF_HARD_ERROR, DEFAULT_HARD_ERROR),
        limit_timeout=conf.get(CONF_LIMIT_TIMEOUT, DEFAULT_LIMIT_TIMEOUT),
        learning_data=learning_data,
        learning_enabled=conf.get(CONF_LEARNING_ENABLED, DEFAULT_LEARNING_ENABLED),
    )

    shadow_controller = MPCShadowController(
        learning=controller.learning,
        deadband=conf.get(CONF_DEADBAND, DEFAULT_DEADBAND),
        min_interval=conf.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
        fan_modes=None,
        enabled=conf.get(CONF_MPC_SHADOW_ENABLED, DEFAULT_MPC_SHADOW_ENABLED),
    )

    # Instantiate the data collector (creates the CSV file immediately if enabled)
    data_collection_enabled = conf.get(CONF_DATA_COLLECTION, DEFAULT_DATA_COLLECTION)
    collector: DataCollector | None = (
        DataCollector(hass.config.config_dir, entry.entry_id)
        if data_collection_enabled
        else None
    )
    if collector:
        _LOGGER.info("DataCollector enabled – writing to %s", collector.path)

    # 3. Store data for platforms and forward setup
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "controller": controller,
        "mpc_shadow": shadow_controller,
        "climate_entity": climate_id,
        "sensor": None,  # Reference will be set in sensor.py
        "store": store,  # Store the storage object for periodic saves
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
        # Check for both None and empty list to handle race condition at startup
        if not controller.fan_modes:
            raw_modes = current_state.attributes.get("fan_modes", [])
            # Remove "auto" from the list of modes
            filtered_modes = [m for m in raw_modes if m.lower() not in ["auto", "off"]]
            if filtered_modes:
                controller.fan_modes = filtered_modes
                shadow_controller.fan_modes = filtered_modes
                _LOGGER.info("Detected fan modes for %s: %s", climate_id, controller.fan_modes)
            else:
                _LOGGER.debug("Climate entity %s has no valid fan modes yet, will retry on next cycle", climate_id)

        # Extract VTherm and Climate data
        attrs = current_state.attributes
        vtherm_slope = attrs.get("specific_states", {}).get("temperature_slope", 0)
        current_temp = attrs.get("current_temperature")
        target_temp = attrs.get("temperature")
        hvac_mode = attrs.get("hvac_mode")
        current_fan = attrs.get("fan_mode")

        # Detect window-open state from VTherm attributes
        # VTherm exposes window_manager.window_state ("on"/"off") and
        # hvac_off_reason ("Window" when heating is cut due to open window)
        window_mgr = attrs.get("window_manager", {})
        is_window_open = (
            window_mgr.get("window_state") == "on" or window_mgr.get("window_auto_state") == "on" or attrs.get("specific_states", {}).get("hvac_off_reason") == "Window"
        )

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
            current_fan,
            is_window_open,
        )

        shadow_decision = shadow_controller.evaluate(
            current_temp=float(current_temp),
            target_temp=float(target_temp),
            vtherm_slope=float(vtherm_slope),
            hvac_mode=str(hvac_mode),
            current_fan=current_fan,
            live_decision_fan=decision.get("fan_mode"),
            is_window_open=is_window_open,
            minutes_since_change=decision.get("minutes_since_last_change", 0.0),
        )
        combined_decision = {**decision, **shadow_decision}

        # Persist one CSV row for offline analysis (beta instrumentation)
        if collector:
            effective_slope = -float(vtherm_slope) if str(hvac_mode) == "cool" else float(vtherm_slope)
            minutes_since = decision.get("minutes_since_last_change", 0.0)
            collector.record(
                hvac_mode=str(hvac_mode),
                current_temp=float(current_temp),
                target_temp=float(target_temp),
                vtherm_slope=float(vtherm_slope),
                is_window_open=is_window_open,
                decision={**decision, "current_fan": current_fan},
                phase=controller.detect_phase(minutes_since),
                effective_slope=effective_slope,
                effective_timeout=controller.get_effective_timeout(),
                force=decision.get("reason", "").startswith(("Emergency", "Setpoint drop")),
                learning_ready=controller.learning_enabled and controller.learning.is_ready(),
                dead_time=controller.learning.get_dead_time() if controller.learning_enabled else 0.0,
                shadow=shadow_decision,
            )

        # Update all sensors stored in the list
        sensors = hass.data[DOMAIN][entry.entry_id].get("sensors")
        if sensors:
            _LOGGER.debug("Updating %s diagnostic sensors", len(sensors))
            for sensor in sensors:
                if hasattr(sensor, 'update_from_controller'):
                    sensor.update_from_controller(combined_decision)
                # Force update all sensors (including learning sensors)
                sensor.async_write_ha_state()

        # Auto-apply learned parameters when learning becomes ready (only once per installation).
        # The flag is persisted in entry.data so it survives reloads and avoids an infinite loop
        # where the reload itself triggers another auto-apply.
        if controller.learning_enabled and controller.learning.is_ready() and not entry.data.get("learning_auto_applied", False):

            optimal = controller.learning.compute_optimal_parameters()
            if optimal:
                _LOGGER.info("Learning is ready! Scheduling auto-apply of learned parameters.")
                hass.async_create_task(_apply_optimal_parameters(hass, entry, optimal))

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
            # Advance the cooldown timer only after the service call succeeds
            controller.confirm_fan_change()

    async def _handle_manual_change(event):
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        new_fan = new_state.attributes.get("fan_mode")
        old_fan = old_state.attributes.get("fan_mode")

        # Ignore transitions where the fan mode disappears (entity going unavailable)
        if new_fan is None or new_fan == old_fan:
            return

        _LOGGER.info("Manual fan_mode change detected, resetting timer")

        # Reset internal controller timer
        manual_data = controller.record_manual_override(new_fan)

        # Instantly refresh sensors to show the change
        sensors = hass.data[DOMAIN][entry.entry_id].get("sensors", [])
        for sensor in sensors:
            if hasattr(sensor, "update_from_controller"):
                sensor.update_from_controller(manual_data)
            sensor.async_write_ha_state()

    # Schedule the loop and run it immediately once to initialize
    remove_timer = async_track_time_interval(hass, run_control_loop, timedelta(minutes=DELTA_TIME_CONTROL_LOOP))
    manual_change = async_track_state_change_event(hass, [climate_id], _handle_manual_change)
    # This ensures the timer stops if the integration is unloaded/removed
    entry.async_on_unload(remove_timer)
    entry.async_on_unload(manual_change)

    # Trigger first run immediately after setup
    hass.async_create_task(run_control_loop(None))

    async def periodic_save_learning(_):
        """Periodically save learning data to persistent storage."""
        learning_data = controller.learning.to_dict()
        await store.async_save(learning_data)
        _LOGGER.debug("Learning data saved to persistent storage")

    # Schedule periodic saves every 5 minutes
    remove_periodic_save = async_track_time_interval(
        hass, periodic_save_learning, LEARNING_DATA_SAVE_INTERVAL
    )
    entry.async_on_unload(remove_periodic_save)

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

        await _apply_optimal_parameters(hass, entry, optimal)

    hass.services.async_register(DOMAIN, "apply_learned_settings", apply_learned_settings)

    # Register service to reset learning data
    async def reset_learning(_):
        """Service to clear all learning data and restart learning."""
        controller.learning.reset()

        # Persist cleared data to both config entry and storage
        learning_data = controller.learning.to_dict()
        new_data = {**entry.data, "learning_data": learning_data}
        hass.config_entries.async_update_entry(entry, data=new_data)
        await store.async_save(learning_data)

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
        store = entry_data.get("store")
        if controller and hasattr(controller, 'learning'):
            learning_data = controller.learning.to_dict()
            # Store in both config entry and persistent storage
            new_data = {**entry.data, "learning_data": learning_data}
            hass.config_entries.async_update_entry(entry, data=new_data)
            if store:
                await store.async_save(learning_data)
                _LOGGER.debug("Learning data saved on unload")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
