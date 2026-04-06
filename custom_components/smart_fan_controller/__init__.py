"""Initialisation of Smart Fan Controller."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CLIMATE_ENTITY,
    CONF_DATA_COLLECTION,
    CONF_DEADBAND,
    CONF_DEFROST_ENTITY,
    CONF_HARD_ERROR,
    CONF_LEARNING_ENABLED,
    CONF_LIMIT_TIMEOUT,
    CONF_MIN_INTERVAL,
    CONF_MPC_PRODUCTION_ENABLED,
    CONF_OPERATING_ENTITY,
    CONF_SOFT_ERROR,
    DEFAULT_DATA_COLLECTION,
    DEFAULT_DEADBAND,
    DEFAULT_HARD_ERROR,
    DEFAULT_LEARNING_ENABLED,
    DEFAULT_LIMIT_TIMEOUT,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_MPC_PRODUCTION_ENABLED,
    DEFAULT_SOFT_ERROR,
    DELTA_TIME_CONTROL_LOOP,
    DOMAIN,
    LEARNING_DATA_SAVE_INTERVAL,
    STORAGE_KEY,
    STORAGE_VERSION,
    build_unique_id,
    extract_object_key_from_unique_id,
)
from .controller import SmartFanController
from .data_collection import DataCollector
from .mpc_controller import MPCController

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


def _filter_supported_fan_modes(raw_modes: list[str] | None) -> list[str]:
    """Keep only supported manual fan modes."""
    if not raw_modes:
        return []
    return [mode for mode in raw_modes if isinstance(mode, str) and mode.lower() not in {"auto", "off"}]


def _extract_supported_fan_modes(state) -> list[str]:
    """Extract supported fan modes from a climate state."""
    if state is None:
        return []
    return _filter_supported_fan_modes(state.attributes.get("fan_modes"))


async def _async_migrate_entity_registry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate Smart Fan Controller entity IDs and unique IDs to the canonical naming."""
    try:
        entity_registry = er.async_get(hass)
    except KeyError:
        _LOGGER.debug(
            "Skipping entity registry migration for %s because the registry is not initialized yet",
            entry.entry_id,
        )
        return

    if not hasattr(entity_registry, "entities"):
        try:
            await entity_registry.async_load()
        except Exception as exc:  # pragma: no cover - defensive guard for partial test harnesses
            _LOGGER.debug(
                "Skipping entity registry migration for %s because the registry is not ready: %s",
                entry.entry_id,
                exc,
            )
            return

    entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    if not entries:
        return

    migrated = 0
    reserved_entity_ids: set[str] = set()

    for registry_entry in entries:
        updates: dict[str, object] = {}
        object_key = extract_object_key_from_unique_id(registry_entry.unique_id, entry.entry_id)

        if object_key is None:
            reserved_entity_ids.add(registry_entry.entity_id)
            _LOGGER.warning(
                "Skipping entity registry migration for %s because its unique_id is not recognized: %s",
                registry_entry.entity_id,
                registry_entry.unique_id,
            )
            continue

        desired_unique_id = build_unique_id(object_key, entry.entry_id)
        if registry_entry.unique_id != desired_unique_id:
            updates["new_unique_id"] = desired_unique_id

        desired_entity_id = entity_registry.async_get_available_entity_id(
            registry_entry.domain,
            f"{DOMAIN}_{object_key}",
            current_entity_id=registry_entry.entity_id,
            reserved_entity_ids=reserved_entity_ids,
        )
        reserved_entity_ids.add(desired_entity_id)

        if registry_entry.entity_id != desired_entity_id:
            updates["new_entity_id"] = desired_entity_id

        if not registry_entry.has_entity_name:
            updates["has_entity_name"] = True

        if not updates:
            continue

        old_entity_id = registry_entry.entity_id
        old_unique_id = registry_entry.unique_id
        updated_entry = entity_registry.async_update_entity(registry_entry.entity_id, **updates)
        migrated += 1
        _LOGGER.info(
            "Migrated entity %s -> %s (unique_id %s -> %s)",
            old_entity_id,
            updated_entry.entity_id,
            old_unique_id,
            updated_entry.unique_id,
        )

    if migrated:
        _LOGGER.info("Entity registry migration completed for %s: %d entities updated", entry.entry_id, migrated)


async def _apply_optimal_parameters(
    hass: HomeAssistant,
    entry: ConfigEntry,
    optimal: dict,
) -> None:
    """Apply learned optimal parameters to the config entry and trigger a reload."""
    new_data = {**entry.data}
    new_data["learning_auto_applied"] = True
    new_data[CONF_DEADBAND] = optimal["deadband"]
    new_data[CONF_SOFT_ERROR] = optimal["soft_error"]
    new_data[CONF_HARD_ERROR] = optimal["hard_error"]
    new_data[CONF_LIMIT_TIMEOUT] = optimal["limit_timeout"]

    _LOGGER.info(
        "Applying learned settings for %s: deadband=%.2f soft_error=%.2f hard_error=%.2f limit_timeout=%d",
        entry.entry_id,
        optimal["deadband"],
        optimal["soft_error"],
        optimal["hard_error"],
        optimal["limit_timeout"],
    )

    hass.config_entries.async_update_entry(entry, data=new_data)
    await hass.config_entries.async_reload(entry.entry_id)


# MPC statuses that mean the controller is paused; in production mode the
# rule-based heuristic is used as fallback when the MPC is in one of these states.
_MPC_PAUSED_STATUSES = frozenset({"Disabled", "Idle", "Disturbed", "Not ready"})


def _read_climate_cycle_data(
    state,
    climate_id: str,
) -> tuple[float, float, float, str, str | None, bool] | None:
    """Return cycle inputs parsed from a climate entity state.

    Returns (vtherm_slope, current_temp, target_temp, hvac_mode, current_fan, is_window_open)
    or None when mandatory data is missing.
    """
    attrs = state.attributes
    vtherm_slope = attrs.get("specific_states", {}).get("temperature_slope", 0)
    current_temp = attrs.get("current_temperature")
    target_temp = attrs.get("temperature")
    hvac_mode = attrs.get("hvac_mode")
    current_fan = attrs.get("fan_mode")

    if vtherm_slope is None:
        _LOGGER.warning("%s is missing VTherm temperature_slope; skipping control cycle", climate_id)
        return None
    if current_temp is None or target_temp is None:
        _LOGGER.debug(
            "Skipping control cycle for %s because temperature data is incomplete (current=%s, target=%s)",
            climate_id,
            current_temp,
            target_temp,
        )
        return None

    window_mgr = attrs.get("window_manager", {})
    is_window_open = window_mgr.get("window_state") == "on" or window_mgr.get("window_auto_state") == "on" or attrs.get("specific_states", {}).get("hvac_off_reason") == "Window"
    return (
        float(vtherm_slope),
        float(current_temp),
        float(target_temp),
        str(hvac_mode),
        current_fan,
        is_window_open,
    )


def _detect_disturbances(hass, conf: dict, controller: SmartFanController) -> bool:
    """Check external entities and update the controller defrost state.

    Returns True when the HVAC compressor is reported as idle.
    """
    defrost_entity_id = conf.get(CONF_DEFROST_ENTITY)
    if defrost_entity_id:
        defrost_state = hass.states.get(defrost_entity_id)
        if defrost_state and defrost_state.state in ("on", "true", "True", "1"):
            if not controller.is_defrost_active:
                _LOGGER.info("External defrost entity %s reports active defrost", defrost_entity_id)
            controller.activate_defrost()

    operating_entity_id = conf.get(CONF_OPERATING_ENTITY)
    if operating_entity_id:
        operating_state = hass.states.get(operating_entity_id)
        if operating_state and operating_state.state in ("off", "false", "False", "0"):
            return True
    return False


def _pick_effective_fan(
    mpc_controller: MPCController,
    decision: dict,
    shadow_decision: dict,
) -> tuple[str, str]:
    """Return (fan_mode, reason) for the current control cycle.

    When MPC production mode is active and the MPC is not paused, the MPC
    decision takes precedence; otherwise the rule-based heuristic is used.
    """
    if mpc_controller.production_mode and shadow_decision.get("mpc_status") not in _MPC_PAUSED_STATUSES and shadow_decision.get("mpc_fan_mode"):
        return shadow_decision["mpc_fan_mode"], f"MPC: {shadow_decision.get('mpc_reason', 'MPC')}"
    return decision["fan_mode"], decision["reason"]


async def _async_apply_fan_change(
    hass,
    climate_id: str,
    effective_fan: str,
    current_fan: str | None,
    reason: str,
    controller: SmartFanController,
) -> None:
    """Send a fan-mode command to the climate entity and confirm on success."""
    _LOGGER.info(
        "Changing %s fan mode from %s to %s (%s)",
        climate_id,
        current_fan,
        effective_fan,
        reason,
    )
    try:
        await hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": climate_id, "fan_mode": effective_fan},
            blocking=True,
        )
    except Exception:  # pylint: disable=broad-exception-caught  # pragma: no cover
        _LOGGER.exception(
            "Failed to change %s fan mode from %s to %s",
            climate_id,
            current_fan,
            effective_fan,
        )
    else:
        controller.confirm_fan_change()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    conf = {**entry.data, **entry.options}
    climate_id = conf[CONF_CLIMATE_ENTITY]
    current_state = hass.states.get(climate_id)
    initial_fan_modes = _extract_supported_fan_modes(current_state)

    _LOGGER.info(
        "Setting up Smart Fan Controller for %s (learning=%s, mpc_production=%s, data_collection=%s)",
        climate_id,
        conf.get(CONF_LEARNING_ENABLED, DEFAULT_LEARNING_ENABLED),
        conf.get(CONF_MPC_PRODUCTION_ENABLED, DEFAULT_MPC_PRODUCTION_ENABLED),
        conf.get(CONF_DATA_COLLECTION, DEFAULT_DATA_COLLECTION),
    )

    if initial_fan_modes:
        _LOGGER.info("Initial fan modes discovered for %s: %s", climate_id, initial_fan_modes)
    else:
        _LOGGER.debug("No supported fan modes available yet for %s during setup", climate_id)

    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")

    learning_data = await store.async_load()
    if not learning_data:
        learning_data = entry.data.get("learning_data")
        _LOGGER.debug("No persistent storage found for %s, using config entry data", entry.entry_id)

    if learning_data:
        _LOGGER.info(
            "Restored learning data for %s (%d slope samples, %d response events)",
            entry.entry_id,
            len(learning_data.get("slope_samples", [])),
            len(learning_data.get("response_events", [])),
        )

    controller = SmartFanController(
        fan_modes=initial_fan_modes or None,
        deadband=conf.get(CONF_DEADBAND, DEFAULT_DEADBAND),
        min_interval=conf.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
        soft_error=conf.get(CONF_SOFT_ERROR, DEFAULT_SOFT_ERROR),
        hard_error=conf.get(CONF_HARD_ERROR, DEFAULT_HARD_ERROR),
        limit_timeout=conf.get(CONF_LIMIT_TIMEOUT, DEFAULT_LIMIT_TIMEOUT),
        learning_data=learning_data,
        learning_enabled=conf.get(CONF_LEARNING_ENABLED, DEFAULT_LEARNING_ENABLED),
    )

    mpc_controller = MPCController(
        learning=controller.learning,
        deadband=conf.get(CONF_DEADBAND, DEFAULT_DEADBAND),
        min_interval=conf.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
        fan_modes=initial_fan_modes or None,
        enabled=True,
    )
    mpc_controller.production_mode = conf.get(CONF_MPC_PRODUCTION_ENABLED, DEFAULT_MPC_PRODUCTION_ENABLED)

    collector: DataCollector | None = DataCollector(hass, hass.config.config_dir, entry.entry_id) if conf.get(CONF_DATA_COLLECTION, DEFAULT_DATA_COLLECTION) else None
    if collector:
        await collector.async_initialize()
        _LOGGER.info("DataCollector enabled for %s, writing to %s", entry.entry_id, collector.path)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "controller": controller,
        "mpc_controller": mpc_controller,
        "climate_entity": climate_id,
        "sensors": [],
        "ensure_profile_sensors": None,
        "store": store,
    }

    await _async_migrate_entity_registry(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    def _ensure_profile_sensors() -> None:
        """Create late-discovered profile sensors when fan modes become available."""
        add_profile_entities = hass.data[DOMAIN][entry.entry_id].get("ensure_profile_sensors")
        if callable(add_profile_entities):
            add_profile_entities()

    async def run_control_loop(_):
        """Main control loop executed every 2 minutes."""
        current_state = hass.states.get(climate_id)
        if not current_state:
            _LOGGER.warning("Climate entity %s not found; skipping control cycle", climate_id)
            return

        if not controller.fan_modes:
            detected_modes = _extract_supported_fan_modes(current_state)
            if detected_modes:
                controller.fan_modes = mpc_controller.fan_modes = detected_modes
                _LOGGER.info("Detected fan modes for %s during runtime: %s", climate_id, detected_modes)
                _ensure_profile_sensors()
            else:
                _LOGGER.debug("No fan modes available yet for %s, retrying next cycle", climate_id)

        cycle_data = _read_climate_cycle_data(current_state, climate_id)
        if cycle_data is None:
            return
        vtherm_slope, current_temp, target_temp, hvac_mode, current_fan, is_window_open = cycle_data
        is_hvac_idle = _detect_disturbances(hass, conf, controller)

        _LOGGER.debug(
            "Cycle start for %s: temp=%.2f target=%.2f slope=%.3f fan=%s hvac=%s window_open=%s",
            climate_id,
            current_temp,
            target_temp,
            vtherm_slope,
            current_fan,
            hvac_mode,
            is_window_open,
        )

        decision = controller.calculate_decision(
            current_temp,
            target_temp,
            vtherm_slope,
            hvac_mode,
            current_fan,
            is_window_open,
            is_hvac_idle,
        )
        shadow_decision = mpc_controller.evaluate(
            current_temp=current_temp,
            target_temp=target_temp,
            vtherm_slope=vtherm_slope,
            hvac_mode=hvac_mode,
            current_fan=current_fan,
            live_decision_fan=decision.get("fan_mode"),
            is_window_open=is_window_open,
            is_defrost_active=controller.is_defrost_active,
            is_hvac_idle=is_hvac_idle,
            minutes_since_change=decision.get("minutes_since_last_change", 0.0),
        )

        _LOGGER.debug(
            "Cycle result for %s: live=%s mpc=%s mpc_status=%s mpc_confidence=%s%%",
            climate_id,
            decision.get("fan_mode"),
            shadow_decision.get("mpc_fan_mode"),
            shadow_decision.get("mpc_status"),
            shadow_decision.get("mpc_confidence"),
        )

        if collector:
            await collector.async_record(
                hvac_mode=hvac_mode,
                current_temp=current_temp,
                target_temp=target_temp,
                vtherm_slope=vtherm_slope,
                is_window_open=is_window_open,
                decision={**decision, "current_fan": current_fan},
                phase=controller.detect_phase(decision.get("minutes_since_last_change", 0.0)),
                effective_slope=-float(vtherm_slope) if hvac_mode == "cool" else float(vtherm_slope),
                effective_timeout=controller.get_effective_timeout(),
                force=decision.get("reason", "").startswith(("Emergency", "Setpoint drop")),
                learning_ready=controller.learning_enabled and controller.learning.is_ready(),
                dead_time=controller.learning.get_dead_time() if controller.learning_enabled else 0.0,
                shadow=shadow_decision,
                defrost_active=controller.is_defrost_active,
                is_hvac_idle=is_hvac_idle,
            )

        for sensor in hass.data[DOMAIN][entry.entry_id].get("sensors") or []:
            if hasattr(sensor, "update_from_controller"):
                sensor.update_from_controller({**decision, **shadow_decision})
            sensor.async_write_ha_state()

        if (
            controller.learning_enabled
            and controller.learning.is_ready()
            and not entry.data.get("learning_auto_applied", False)
        ):
            learned_params = controller.learning.compute_optimal_parameters()
            if learned_params:
                _LOGGER.info("Learning is ready for %s, scheduling auto-apply of learned parameters", climate_id)
                hass.async_create_task(_apply_optimal_parameters(hass, entry, learned_params))

        effective_fan, effective_reason = _pick_effective_fan(mpc_controller, decision, shadow_decision)
        if effective_fan != current_fan:
            await _async_apply_fan_change(hass, climate_id, effective_fan, current_fan, effective_reason, controller)
        else:
            _LOGGER.debug("No fan mode change required for %s (%s)", climate_id, effective_reason)

    async def _handle_manual_change(event):
        """Track manual fan mode changes to reset the controller cooldown."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return

        new_fan = new_state.attributes.get("fan_mode")
        old_fan = old_state.attributes.get("fan_mode")

        if new_fan is None or new_fan == old_fan:
            return

        _LOGGER.info("Manual fan mode change detected for %s: %s -> %s", climate_id, old_fan, new_fan)

        manual_data = controller.record_manual_override(new_fan)

        sensors = hass.data[DOMAIN][entry.entry_id].get("sensors", [])
        for sensor in sensors:
            if hasattr(sensor, "update_from_controller"):
                sensor.update_from_controller(manual_data)
            sensor.async_write_ha_state()

    entry.async_on_unload(async_track_time_interval(hass, run_control_loop, timedelta(minutes=DELTA_TIME_CONTROL_LOOP)))
    entry.async_on_unload(async_track_state_change_event(hass, [climate_id], _handle_manual_change))

    hass.async_create_task(run_control_loop(None))

    async def periodic_save_learning(_):
        """Periodically save learning data to persistent storage."""
        learning_data_to_save = controller.learning.to_dict()
        await store.async_save(learning_data_to_save)
        _LOGGER.debug(
            "Persisted learning data for %s (%d slope samples, %d response events)",
            entry.entry_id,
            len(learning_data_to_save.get("slope_samples", [])),
            len(learning_data_to_save.get("response_events", [])),
        )

    entry.async_on_unload(async_track_time_interval(hass, periodic_save_learning, LEARNING_DATA_SAVE_INTERVAL))

    async def apply_learned_settings(_):
        """Service to apply optimal parameters from learning."""
        if not controller.learning.is_ready():
            _LOGGER.warning(
                "Learning not complete yet for %s (%.1f%%), cannot apply settings",
                climate_id,
                controller.learning.get_progress(),
            )
            return

        optimal = controller.learning.compute_optimal_parameters()
        if not optimal:
            _LOGGER.error("Failed to compute optimal parameters for %s", climate_id)
            return

        await _apply_optimal_parameters(hass, entry, optimal)

    hass.services.async_register(DOMAIN, "apply_learned_settings", apply_learned_settings)

    async def reset_learning(_):
        """Service to clear all learning data and restart learning."""
        controller.learning.reset()

        learning_data_to_save = controller.learning.to_dict()
        new_data = {**entry.data, "learning_data": learning_data_to_save}
        hass.config_entries.async_update_entry(entry, data=new_data)
        await store.async_save(learning_data_to_save)

        sensors = hass.data[DOMAIN][entry.entry_id].get("sensors", [])
        for sensor in sensors:
            sensor.async_write_ha_state()

        _LOGGER.info("Learning reset for %s: all samples and stats cleared", climate_id)

    hass.services.async_register(DOMAIN, "reset_learning", reset_learning)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry when it's being removed."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        controller = entry_data.get("controller")
        store = entry_data.get("store")
        if controller and hasattr(controller, "learning"):
            learning_data = controller.learning.to_dict()
            new_data = {**entry.data, "learning_data": learning_data}
            hass.config_entries.async_update_entry(entry, data=new_data)
            if store:
                await store.async_save(learning_data)
                _LOGGER.debug(
                    "Learning data saved on unload for %s (%d slope samples, %d response events)",
                    entry.entry_id,
                    len(learning_data.get("slope_samples", [])),
                    len(learning_data.get("response_events", [])),
                )

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.info("Unloaded Smart Fan Controller entry %s", entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.info("Reloading Smart Fan Controller entry %s", entry.entry_id)
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
