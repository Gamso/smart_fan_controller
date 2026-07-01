"""Initialisation of Smart Fan Controller."""
from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CLIMATE_ENTITY,
    CONF_DATA_COLLECTION,
    CONF_DEADBAND,
    CONF_DEFROST_ENTITY,
    CONF_LIMIT_TIMEOUT,
    CONF_MIN_INTERVAL,
    CONF_OPERATING_ENTITY,
    DEAD_TIME_SAFETY_FACTOR,
    DEFAULT_DATA_COLLECTION,
    DEFAULT_DEAD_TIME,
    DEFAULT_DEADBAND,
    DEFAULT_LIMIT_TIMEOUT,
    DEFAULT_MIN_INTERVAL,
    DELTA_TIME_CONTROL_LOOP,
    DOMAIN,
    LEARNING_DATA_SAVE_INTERVAL,
    MIN_ESTABLISHED_RATIO,
    PHASE_ESTABLISHED,
    SETPOINT_DROP_LEARNING_COOLDOWN,
    STORAGE_KEY,
    STORAGE_VERSION,
    THRESHOLD_SLOPE,
    THRESHOLD_TARGET_DROP,
    build_unique_id,
    build_entity_id,
    build_scoped_entity_id,
    extract_object_key_from_unique_id,
)
from .data_collection import DataCollector
from .mpc_controller import MPCController
from .thermal_learning import ThermalLearning

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
SERVICE_APPLY_LEARNED_SETTINGS = "apply_learned_settings"
SERVICE_RESET_LEARNING = "reset_learning"
SERVICE_SET_EFFECTIVE_SLOPE = "set_effective_slope"
SERVICE_FORCE_FAN = "force_fan"


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


def _iter_loaded_entries(hass: HomeAssistant) -> list[tuple[str, dict]]:
    """Return loaded Smart Fan Controller entries, excluding internal metadata."""
    domain_data = hass.data.get(DOMAIN, {})
    return [
        (entry_id, entry_data)
        for entry_id, entry_data in domain_data.items()
        if not entry_id.startswith("_")
    ]


def _resolve_controller_entry(
    hass: HomeAssistant,
    climate_entity: str | None = None,
) -> tuple[str, dict]:
    """Resolve one loaded controller entry for a service call."""
    entries = _iter_loaded_entries(hass)
    if not entries:
        raise HomeAssistantError("No Smart Fan Controller entry is currently loaded")

    if climate_entity is not None:
        matches = [
            (entry_id, entry_data)
            for entry_id, entry_data in entries
            if entry_data.get("climate_entity") == climate_entity
        ]
        if not matches:
            raise HomeAssistantError(
                f"No Smart Fan Controller entry is configured for {climate_entity}"
            )
        if len(matches) > 1:
            raise HomeAssistantError(
                f"Multiple Smart Fan Controller entries are configured for {climate_entity}; remove duplicates first"
            )
        return matches[0]

    if len(entries) > 1:
        raise HomeAssistantError(
            "Multiple Smart Fan Controller entries are configured; specify climate_entity in the service call"
        )

    return entries[0]


def _build_data_collection_decision(
    *,
    effective_fan: str | None,
    effective_reason: str,
    current_fan: str | None,
    current_error: float,
    minutes_since_change: float,
    hvac_mode: str,
    target_temp: float,
    mpc_decision: dict,
) -> dict:
    """Build the audit payload written to the CSV collector."""
    projected_temperature = mpc_decision.get("mpc_predicted_temperature_10m")
    projected_error = None

    if projected_temperature is not None:
        projected_error = (
            projected_temperature - target_temp
            if hvac_mode == "cool"
            else target_temp - projected_temperature
        )

    return {
        "fan_mode": effective_fan,
        "reason": effective_reason,
        "current_fan": current_fan,
        "temperature_error": current_error,
        "projected_temperature": projected_temperature,
        "projected_temperature_error": projected_error,
        "minutes_since_last_change": minutes_since_change,
    }


def _is_legacy_generated_entity_id(entity_id: str, platform_domain: str, object_key: str) -> bool:
    """Return True when the entity_id matches the old auto-generated naming scheme."""
    legacy_entity_id = build_entity_id(platform_domain, object_key)
    return re.fullmatch(rf"{re.escape(legacy_entity_id)}(?:_\d+)?", entity_id) is not None


async def _async_migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry, climate_entity: str) -> None:
    """Migrate legacy entity_ids to the climate-scoped naming scheme."""
    entity_registry = er.async_get(hass)

    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        object_key = extract_object_key_from_unique_id(entity_entry.unique_id, entry.entry_id)
        if object_key is None:
            continue

        desired_entity_id = build_scoped_entity_id(entity_entry.domain, climate_entity, object_key)
        if entity_entry.entity_id == desired_entity_id:
            continue

        if not _is_legacy_generated_entity_id(entity_entry.entity_id, entity_entry.domain, object_key):
            continue

        suggested_object_id = desired_entity_id.split(".", maxsplit=1)[1]
        available_entity_id = entity_registry.async_get_available_entity_id(
            entity_entry.domain,
            suggested_object_id,
            current_entity_id=entity_entry.entity_id,
        )
        entity_registry.async_update_entity(
            entity_entry.entity_id,
            new_entity_id=available_entity_id,
        )
        _LOGGER.info(
            "Migrated %s entity_id from %s to %s",
            entry.entry_id,
            entity_entry.entity_id,
            available_entity_id,
        )


def _should_collect_slope_sample(
    *,
    current_fan: str | None,
    is_defrost_active: bool,
    is_hvac_idle: bool,
    phase: str,
    minutes_since_change: float,
    learned_dead_time: float,
    ctrl_state: dict,
    now: float,
) -> bool:
    """Return True when slope learning conditions are met."""
    if current_fan is None or is_defrost_active or is_hvac_idle:
        return False
    if phase != PHASE_ESTABLISHED:
        return False
    min_stable_minutes = learned_dead_time * MIN_ESTABLISHED_RATIO
    if minutes_since_change < min_stable_minutes:
        return False
    if ctrl_state["last_setpoint_drop_time"] != 0:
        minutes_since_setpoint_drop = (now - ctrl_state["last_setpoint_drop_time"]) / 60.0
        if minutes_since_setpoint_drop < SETPOINT_DROP_LEARNING_COOLDOWN:
            return False
    return True


def _update_sensors(hass: HomeAssistant, entry_id: str, data: dict) -> None:
    """Push new data to all sensor entities and trigger a state write."""
    for sensor in hass.data[DOMAIN][entry_id].get("sensors") or []:
        if hasattr(sensor, "update_from_mpc"):
            sensor.update_from_mpc(data)
        sensor.async_write_ha_state()


async def _apply_optimal_parameters(
    hass: HomeAssistant,
    entry: ConfigEntry,
    optimal: dict,
) -> None:
    """Apply learned optimal parameters to the config entry and trigger a reload."""
    new_data = {**entry.data}
    new_data["learning_auto_applied"] = True
    new_data[CONF_DEADBAND] = optimal["deadband"]
    new_data[CONF_LIMIT_TIMEOUT] = optimal["limit_timeout"]
    new_options = {**entry.options}
    new_options[CONF_DEADBAND] = optimal["deadband"]
    new_options[CONF_LIMIT_TIMEOUT] = optimal["limit_timeout"]

    _LOGGER.info(
        "Applying learned settings for %s: deadband=%.2f limit_timeout=%d",
        entry.entry_id,
        optimal["deadband"],
        optimal["limit_timeout"],
    )

    hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services once per Home Assistant instance."""

    async def apply_learned_settings(call):
        """Service to apply optimal parameters from learning."""
        _, entry_data = _resolve_controller_entry(hass, call.data.get(CONF_CLIMATE_ENTITY))
        learning = entry_data["learning"]
        entry = entry_data["entry"]
        climate_id = entry_data["climate_entity"]

        if not learning.is_ready():
            _LOGGER.warning(
                "Learning not complete yet for %s (%.1f%%), cannot apply settings",
                climate_id,
                learning.get_progress(),
            )
            return

        optimal = learning.compute_optimal_parameters()
        if not optimal:
            _LOGGER.error("Failed to compute optimal parameters for %s", climate_id)
            return

        await _apply_optimal_parameters(hass, entry, optimal)

    async def reset_learning(call):
        """Service to clear all learning data and restart learning."""
        entry_id, entry_data = _resolve_controller_entry(hass, call.data.get(CONF_CLIMATE_ENTITY))
        learning = entry_data["learning"]
        entry = entry_data["entry"]
        store = entry_data["store"]
        climate_id = entry_data["climate_entity"]

        learning.reset()

        learning_data_to_save = learning.to_dict()
        new_data = {**entry.data, "learning_data": learning_data_to_save}
        new_data.pop("learning_auto_applied", None)
        hass.config_entries.async_update_entry(entry, data=new_data)
        await store.async_save(learning_data_to_save)

        sensors = hass.data[DOMAIN][entry_id].get("sensors", [])
        for sensor in sensors:
            sensor.async_write_ha_state()

        _LOGGER.info("Learning reset for %s: all samples and stats cleared", climate_id)

    async def set_effective_slope(call):
        """Service to manually set the effective slope for a fan/HVAC profile."""
        entry_id, entry_data = _resolve_controller_entry(hass, call.data.get(CONF_CLIMATE_ENTITY))
        learning = entry_data["learning"]
        store = entry_data["store"]
        hvac_mode = call.data["hvac_mode"]
        fan_mode = call.data["fan_mode"]
        effective_slope = float(call.data["effective_slope"])

        learning.set_mode_effective_slope(fan_mode, hvac_mode, effective_slope)

        learning_data_to_save = learning.to_dict()
        await store.async_save(learning_data_to_save)

        sensors = hass.data[DOMAIN][entry_id].get("sensors", [])
        for sensor in sensors:
            sensor.async_write_ha_state()

        _LOGGER.info(
            "Set effective slope for %s/%s to %.3f", hvac_mode, fan_mode, effective_slope
        )

    async def force_fan(call):
        """Service to force a specific fan mode for a fixed duration (minutes).

        A duration of 0 cancels any active override and hands control back to the MPC.
        """
        entry_id, entry_data = _resolve_controller_entry(hass, call.data.get(CONF_CLIMATE_ENTITY))
        mpc_controller = entry_data["mpc_controller"]
        climate_id = entry_data["climate_entity"]
        fan_mode = call.data["fan_mode"]
        duration_minutes = float(call.data["duration_minutes"])

        if duration_minutes <= 0:
            entry_data["force"] = None
            _LOGGER.info("Force fan cancelled for %s; resuming MPC control", climate_id)
        else:
            available = mpc_controller.fan_modes or []
            if available and fan_mode not in available:
                raise HomeAssistantError(
                    f"Fan mode '{fan_mode}' is not available for {climate_id}. "
                    f"Known fan modes: {', '.join(available) or 'none yet'}"
                )
            entry_data["force"] = {"fan_mode": fan_mode, "until": time.time() + duration_minutes * 60.0}
            _LOGGER.info(
                "Forcing fan '%s' for %s for %.0f min", fan_mode, climate_id, duration_minutes
            )

        # Apply immediately instead of waiting for the next 2-minute cycle.
        trigger_cycle = entry_data.get("trigger_cycle")
        if callable(trigger_cycle):
            trigger_cycle()

    hass.services.async_register(DOMAIN, SERVICE_APPLY_LEARNED_SETTINGS, apply_learned_settings)
    hass.services.async_register(DOMAIN, SERVICE_RESET_LEARNING, reset_learning)
    hass.services.async_register(DOMAIN, SERVICE_SET_EFFECTIVE_SLOPE, set_effective_slope)
    hass.services.async_register(DOMAIN, SERVICE_FORCE_FAN, force_fan)


# MPC statuses that mean the controller is paused; the current fan mode is
# held when the MPC is in one of these states.
_MPC_PAUSED_STATUSES = frozenset({"Idle", "Disturbed", "Not ready"})


def _resolve_active_force(
    entry_data: dict,
    fan_modes: list[str] | None,
    now: float,
    climate_id: str,
) -> str | None:
    """Return the forced fan mode if a force override is currently active, else None.

    Clears expired or invalid overrides as a side effect so control returns to the MPC.
    """
    force = entry_data.get("force")
    if not force:
        return None

    if now >= force["until"]:
        entry_data["force"] = None
        _LOGGER.info("Force fan expired for %s; resuming MPC control", climate_id)
        return None

    forced_fan = force["fan_mode"]
    if fan_modes and forced_fan not in fan_modes:
        entry_data["force"] = None
        _LOGGER.warning(
            "Forced fan '%s' is no longer a valid mode for %s; cancelling override",
            forced_fan,
            climate_id,
        )
        return None

    return forced_fan


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

    # VTherm can briefly expose non-numeric values ("unknown"/"unavailable")
    # during restarts; guard the conversion so a transient state skips the
    # cycle instead of raising and aborting the control loop.
    try:
        vtherm_slope_value = float(vtherm_slope)
        current_temp_value = float(current_temp)
        target_temp_value = float(target_temp)
    except (TypeError, ValueError):
        _LOGGER.debug(
            "Skipping control cycle for %s: non-numeric climate data (slope=%s, current=%s, target=%s)",
            climate_id,
            vtherm_slope,
            current_temp,
            target_temp,
        )
        return None

    window_mgr = attrs.get("window_manager", {})
    is_window_open = window_mgr.get("window_state") == "on" or window_mgr.get("window_auto_state") == "on" or attrs.get("specific_states", {}).get("hvac_off_reason") == "Window"
    return (
        vtherm_slope_value,
        current_temp_value,
        target_temp_value,
        str(hvac_mode),
        current_fan,
        is_window_open,
    )


def _detect_disturbances(hass, conf: dict, defrost_state: dict) -> tuple[bool, bool]:
    """Check external entities and update defrost state dict.

    Returns (is_defrost_active, is_hvac_idle).
    defrost_state dict keys: 'active', 'start_time'.
    """
    now_ts = time.time()
    defrost_entity_id = conf.get(CONF_DEFROST_ENTITY)
    if defrost_entity_id:
        entity_state = hass.states.get(defrost_entity_id)
        if entity_state and entity_state.state in ("on", "true", "True", "1"):
            if not defrost_state["active"]:
                _LOGGER.info("External defrost entity %s reports active defrost", defrost_entity_id)
            defrost_state["active"] = True
            defrost_state["start_time"] = now_ts

    is_defrost = defrost_state["active"]
    if is_defrost:
        elapsed = (now_ts - defrost_state["start_time"]) / 60.0
        if elapsed > 20.0:
            defrost_state["active"] = False
            _LOGGER.debug("Defrost cooldown expired after %.1f min", elapsed)
            is_defrost = False

    is_hvac_idle = False
    operating_entity_id = conf.get(CONF_OPERATING_ENTITY)
    if operating_entity_id:
        operating_state = hass.states.get(operating_entity_id)
        if operating_state and operating_state.state in ("off", "false", "False", "0", "idle"):
            is_hvac_idle = True

    return is_defrost, is_hvac_idle


async def _async_apply_fan_change(
    hass,
    climate_id: str,
    effective_fan: str,
    current_fan: str | None,
    reason: str,
    on_confirmed,
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
        on_confirmed()  # update last_change_time to prevent retry storm
    else:
        on_confirmed()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    conf = {**entry.data, **entry.options}
    domain_data = hass.data.setdefault(DOMAIN, {})
    climate_id = conf[CONF_CLIMATE_ENTITY]
    current_state = hass.states.get(climate_id)
    initial_fan_modes = _extract_supported_fan_modes(current_state)

    _LOGGER.info(
        "Setting up Smart Fan Controller for %s (data_collection=%s)",
        climate_id,
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

    learning = ThermalLearning.from_dict(learning_data) if learning_data else ThermalLearning()

    mpc_controller = MPCController(
        learning=learning,
        deadband=conf.get(CONF_DEADBAND, DEFAULT_DEADBAND),
        min_interval=conf.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
        limit_timeout=conf.get(CONF_LIMIT_TIMEOUT, DEFAULT_LIMIT_TIMEOUT),
        fan_modes=initial_fan_modes or None,
    )

    collector: DataCollector | None = DataCollector(hass, hass.config.config_dir, entry.entry_id) if conf.get(CONF_DATA_COLLECTION, DEFAULT_DATA_COLLECTION) else None
    if collector:
        await collector.async_initialize()
        _LOGGER.info("DataCollector enabled for %s, writing to %s", entry.entry_id, collector.path)

    domain_data[entry.entry_id] = {
        "entry": entry,
        "learning": learning,
        "mpc_controller": mpc_controller,
        "climate_entity": climate_id,
        "sensors": [],
        "ensure_profile_sensors": None,
        "store": store,
        # Manual override set by the force_fan service: {"fan_mode": str, "until": float}
        "force": None,
        "trigger_cycle": None,
    }

    if not hass.services.has_service(DOMAIN, SERVICE_APPLY_LEARNED_SETTINGS):
        _register_services(hass)

    await _async_migrate_entity_ids(hass, entry, climate_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    def _ensure_profile_sensors() -> None:
        """Create late-discovered profile sensors when fan modes become available."""
        add_profile_entities = hass.data[DOMAIN][entry.entry_id].get("ensure_profile_sensors")
        if callable(add_profile_entities):
            add_profile_entities()

    # Mutable state shared between control-loop callbacks (closure variables)
    ctrl_state: dict = {
        "last_change_time": time.time() - (conf.get(CONF_LIMIT_TIMEOUT, DEFAULT_LIMIT_TIMEOUT) * 60),
        "last_setpoint_drop_time": 0.0,
        "previous_slope": None,
        "last_hvac_mode": None,
        "defrost": {"active": False, "start_time": 0.0},
        # Fan mode the controller itself just commanded, so the state-change
        # listener can tell its own change apart from a genuine manual override.
        "controller_commanded_fan": None,
        # Re-entrancy guard: True while a cycle is executing so overlapping
        # triggers (periodic timer, startup run, force_fan) do not run concurrently.
        "cycle_running": False,
    }

    async def _execute_control_cycle():
        """Run one full control cycle (guarded by run_control_loop)."""
        current_state = hass.states.get(climate_id)
        if not current_state:
            _LOGGER.warning("Climate entity %s not found; skipping control cycle", climate_id)
            return

        if not mpc_controller.fan_modes:
            detected_modes = _extract_supported_fan_modes(current_state)
            if detected_modes:
                mpc_controller.fan_modes = detected_modes
                _LOGGER.info("Detected fan modes for %s during runtime: %s", climate_id, detected_modes)
                _ensure_profile_sensors()
            else:
                _LOGGER.debug("No fan modes available yet for %s, retrying next cycle", climate_id)

        cycle_data = _read_climate_cycle_data(current_state, climate_id)
        if cycle_data is None:
            return
        vtherm_slope, current_temp, target_temp, hvac_mode, current_fan, is_window_open = cycle_data
        is_defrost_active, is_hvac_idle = _detect_disturbances(hass, conf, ctrl_state["defrost"])

        now = time.time()
        minutes_since_change = (now - ctrl_state["last_change_time"]) / 60.0

        # Reset slope memory on HVAC mode switch
        if ctrl_state["last_hvac_mode"] is not None and ctrl_state["last_hvac_mode"] != hvac_mode:
            _LOGGER.info("HVAC mode changed %s -> %s: resetting slope memory", ctrl_state["last_hvac_mode"], hvac_mode)
            ctrl_state["previous_slope"] = None
        ctrl_state["last_hvac_mode"] = hvac_mode

        if ctrl_state["previous_slope"] is None:
            ctrl_state["previous_slope"] = vtherm_slope
        slope_change = abs(vtherm_slope - ctrl_state["previous_slope"]) > THRESHOLD_SLOPE

        # Signed comfort error (positive = need more heating/cooling)
        current_error = (current_temp - target_temp) if hvac_mode == "cool" else (target_temp - current_temp)

        # Track setpoint-drop events for learning cooldown
        if current_error < THRESHOLD_TARGET_DROP:
            ctrl_state["last_setpoint_drop_time"] = now

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

        mpc_decision = mpc_controller.evaluate(
            current_temp=current_temp,
            target_temp=target_temp,
            vtherm_slope=vtherm_slope,
            hvac_mode=hvac_mode,
            current_fan=current_fan,
            is_window_open=is_window_open,
            is_defrost_active=is_defrost_active,
            is_hvac_idle=is_hvac_idle,
            minutes_since_change=minutes_since_change,
        )

        _LOGGER.debug(
            "Cycle result for %s: mpc=%s mpc_status=%s mpc_confidence=%s%%",
            climate_id,
            mpc_decision.get("mpc_fan_mode"),
            mpc_decision.get("mpc_status"),
            mpc_decision.get("mpc_confidence"),
        )

        # Manual override: the force_fan service can pin a fan mode for a fixed window.
        entry_data = domain_data[entry.entry_id]
        forced_fan = _resolve_active_force(entry_data, mpc_controller.fan_modes, now, climate_id)

        # Determine effective fan: forced > MPC (when actionable) > hold current
        if forced_fan is not None:
            remaining_min = (entry_data["force"]["until"] - now) / 60.0
            effective_fan = forced_fan
            effective_reason = f"Forced fan '{forced_fan}' ({remaining_min:.0f} min left)"
            mpc_decision = {
                **mpc_decision,
                "mpc_status": "Forced",
                "mpc_fan_mode": forced_fan,
                "mpc_reason": effective_reason,
                "mpc_would_change_now": "yes" if forced_fan != current_fan else "no",
            }
        elif mpc_decision.get("mpc_status") not in _MPC_PAUSED_STATUSES and mpc_decision.get("mpc_fan_mode"):
            effective_fan = mpc_decision["mpc_fan_mode"]
            effective_reason = f"MPC: {mpc_decision.get('mpc_reason', 'MPC')}"
        else:
            effective_fan = current_fan
            effective_reason = f"MPC paused: {mpc_decision.get('mpc_status', 'unknown')}"

        # Phase classification (for learning gating and data collection)
        learned_dead_time = learning.get_dead_time(hvac_mode) if learning.is_ready() else DEFAULT_DEAD_TIME
        if minutes_since_change < learned_dead_time:
            phase = "DEAD_TIME"
        elif minutes_since_change < learned_dead_time * DEAD_TIME_SAFETY_FACTOR:
            phase = "TRANSIENT"
        else:
            phase = PHASE_ESTABLISHED

        # Slope sample collection
        if _should_collect_slope_sample(
            current_fan=current_fan,
            is_defrost_active=is_defrost_active,
            is_hvac_idle=is_hvac_idle,
            phase=phase,
            minutes_since_change=minutes_since_change,
            learned_dead_time=learned_dead_time,
            ctrl_state=ctrl_state,
            now=now,
        ):  # current_fan is guaranteed non-None by _should_collect_slope_sample
            learning.add_slope_sample(current_fan, vtherm_slope, current_error, hvac_mode, is_window_open)  # type: ignore[arg-type]

        # Response event collection
        if slope_change and ctrl_state["last_change_time"] > 0:
            response_time = minutes_since_change
            if 2.0 <= response_time <= 60.0 and not is_window_open and not is_defrost_active and not is_hvac_idle:
                learning.add_response_event(response_time, hvac_mode)
                _LOGGER.debug("Recorded thermal response event: %.1f min after last fan change (hvac=%s)", response_time, hvac_mode)

        if slope_change:
            ctrl_state["previous_slope"] = vtherm_slope

        if collector:
            collector_decision = _build_data_collection_decision(
                effective_fan=effective_fan,
                effective_reason=effective_reason,
                current_fan=current_fan,
                current_error=current_error,
                minutes_since_change=minutes_since_change,
                hvac_mode=hvac_mode,
                target_temp=target_temp,
                mpc_decision=mpc_decision,
            )
            await collector.async_record(
                hvac_mode=hvac_mode,
                current_temp=current_temp,
                target_temp=target_temp,
                vtherm_slope=vtherm_slope,
                is_window_open=is_window_open,
                decision=collector_decision,
                phase=phase,
                effective_slope=-float(vtherm_slope) if hvac_mode == "cool" else float(vtherm_slope),
                effective_timeout=mpc_controller.get_effective_timeout(hvac_mode),
                force=forced_fan is not None,
                learning_ready=learning.is_ready(),
                dead_time=learning.get_dead_time(hvac_mode),
                mpc_decision=mpc_decision,
                defrost_active=is_defrost_active,
                is_hvac_idle=is_hvac_idle,
            )

        _update_sensors(hass, entry.entry_id, {
            **mpc_decision,
            "fan_mode": effective_fan,
            "minutes_since_last_change": round(minutes_since_change, 2),
        })

        if learning.is_ready() and not entry.data.get("learning_auto_applied", False):
            learned_params = learning.compute_optimal_parameters()
            if learned_params:
                _LOGGER.info("Learning is ready for %s, scheduling auto-apply of learned parameters", climate_id)
                hass.async_create_task(_apply_optimal_parameters(hass, entry, learned_params))

        if effective_fan is not None and effective_fan != current_fan:

            def _confirm():
                ctrl_state["last_change_time"] = time.time()
                _LOGGER.debug("Confirmed fan change for %s at %.3f", climate_id, ctrl_state["last_change_time"])

            # Mark this as a controller-initiated change so the resulting
            # state-change event is not mislabelled as a manual override.
            ctrl_state["controller_commanded_fan"] = effective_fan
            await _async_apply_fan_change(hass, climate_id, effective_fan, current_fan, effective_reason, _confirm)
        else:
            _LOGGER.debug("No fan mode change required for %s (%s)", climate_id, effective_reason)

    async def run_control_loop(_=None):
        """Guarded entry point: run one cycle unless one is already in progress.

        Skipping (rather than queuing) prevents overlapping triggers from racing
        on shared state or issuing duplicate fan commands. A skipped force_fan
        trigger is still applied by the next cycle, since the override is stored.
        """
        if ctrl_state["cycle_running"]:
            _LOGGER.debug("Control cycle already in progress for %s; skipping overlapping trigger", climate_id)
            return
        ctrl_state["cycle_running"] = True
        try:
            await _execute_control_cycle()
        finally:
            ctrl_state["cycle_running"] = False

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

        # Ignore the state change produced by the controller's own command so it
        # is not reported as a manual override.
        if new_fan == ctrl_state.get("controller_commanded_fan"):
            ctrl_state["controller_commanded_fan"] = None
            _LOGGER.debug("Ignoring controller-initiated fan change for %s: %s -> %s", climate_id, old_fan, new_fan)
            return

        _LOGGER.info("Manual fan mode change detected for %s: %s -> %s", climate_id, old_fan, new_fan)
        ctrl_state["last_change_time"] = time.time()
        manual_data = {"fan_mode": new_fan, "minutes_since_last_change": 0.0, "reason": "Manual Override"}

        _update_sensors(hass, entry.entry_id, manual_data)

    # Expose a way for the force_fan service to apply an override immediately.
    domain_data[entry.entry_id]["trigger_cycle"] = lambda: hass.async_create_task(run_control_loop(None))

    entry.async_on_unload(async_track_time_interval(hass, run_control_loop, timedelta(minutes=DELTA_TIME_CONTROL_LOOP)))
    entry.async_on_unload(async_track_state_change_event(hass, [climate_id], _handle_manual_change))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    hass.async_create_task(run_control_loop(None))

    async def periodic_save_learning(_):
        """Periodically save learning data to persistent storage."""
        learning_data_to_save = learning.to_dict()
        await store.async_save(learning_data_to_save)
        _LOGGER.debug(
            "Persisted learning data for %s (%d slope samples, %d response events)",
            entry.entry_id,
            len(learning_data_to_save.get("slope_samples", [])),
            len(learning_data_to_save.get("response_events", [])),
        )

    entry.async_on_unload(async_track_time_interval(hass, periodic_save_learning, LEARNING_DATA_SAVE_INTERVAL))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry when it's being removed."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data:
        learning = entry_data.get("learning")
        store = entry_data.get("store")
        if learning is not None:
            learning_data = learning.to_dict()
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
        domain_data = hass.data[DOMAIN]
        domain_data.pop(entry.entry_id, None)
        if not _iter_loaded_entries(hass):
            hass.services.async_remove(DOMAIN, SERVICE_APPLY_LEARNED_SETTINGS)
            hass.services.async_remove(DOMAIN, SERVICE_RESET_LEARNING)
            hass.services.async_remove(DOMAIN, SERVICE_SET_EFFECTIVE_SLOPE)
            hass.services.async_remove(DOMAIN, SERVICE_FORCE_FAN)
            if not domain_data:
                hass.data.pop(DOMAIN, None)
        _LOGGER.info("Unloaded Smart Fan Controller entry %s", entry.entry_id)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    _LOGGER.info("Reloading Smart Fan Controller entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
