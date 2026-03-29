"""Sensor platform for Smart Fan Controller."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MIN_MODE_PROFILE_SAMPLES


class _SmartFanEntity(SensorEntity):
    """Base sensor that wires every subclass to the Smart Fan Controller device."""

    _entry_id: str

    @property
    def device_info(self) -> DeviceInfo:
        """Link the entity to the Smart Fan Controller device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Smart Fan Controller",
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]

    sensor_definitions = [
        ("Status", "reason", None, None, "mdi:information-outline", EntityCategory.DIAGNOSTIC),
        ("Fan Mode", "fan_mode", None, SensorDeviceClass.ENUM, "mdi:fan", None),
        ("Fan Mode - Last change", "minutes_since_last_change", UnitOfTime.MINUTES, SensorDeviceClass.DURATION, "mdi:clock-outline", EntityCategory.DIAGNOSTIC),
        ("Temperature Projected (10 min)", "projected_temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:chart-bell-curve", EntityCategory.DIAGNOSTIC),
        ("Temperature Projected Error (10 min)", "projected_temperature_error", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:chart-bell-curve", EntityCategory.DIAGNOSTIC),
        ("Temperature Error", "temperature_error", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:thermometer-lines", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Status", "mpc_shadow_status", None, None, "mdi:robot-outline", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Reason", "mpc_shadow_reason", None, None, "mdi:text-box-search-outline", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Fan Mode", "mpc_shadow_fan_mode", None, None, "mdi:fan-chevron-up", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Match", "mpc_shadow_matches_live", None, None, "mdi:compare", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Would Change Now", "mpc_shadow_would_change_now", None, None, "mdi:swap-horizontal", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Cost", "mpc_shadow_cost", None, None, "mdi:calculator", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Confidence", "mpc_shadow_confidence", PERCENTAGE, None, "mdi:chart-line", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Predicted Temperature (10 min)", "mpc_shadow_predicted_temperature_10m", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:chart-timeline-variant", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Predicted Temperature (30 min)", "mpc_shadow_predicted_temperature_30m", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:chart-timeline-variant", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Dead Time", "mpc_shadow_dead_time", UnitOfTime.MINUTES, SensorDeviceClass.DURATION, "mdi:timer-sand", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Known Profiles", "mpc_shadow_known_profiles", None, None, "mdi:database-search-outline", EntityCategory.DIAGNOSTIC),
        ("MPC Shadow Disturbance Bias", "mpc_shadow_disturbance_bias", "°C/h", None, "mdi:weather-windy", EntityCategory.DIAGNOSTIC),
    ]

    entities = []
    for name, key, unit, device_class, icon, entity_category in sensor_definitions:
        entities.append(SmartFanSensor(entry.entry_id, name, key, unit, device_class, icon, entity_category))

    controller = data["controller"]
    entities.append(SmartFanLearningSensor(entry.entry_id, controller))
    entities.append(SmartFanLearningStatusSensor(entry.entry_id, controller))
    entities.append(SmartFanLearningSamplesSensor(entry.entry_id, controller))
    entities.append(SmartFanLearningResponseSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedDeadTimeSensor(entry.entry_id, controller))
    entities.append(SmartFanEffectiveTimeoutSensor(entry.entry_id, controller))
    entities.append(SmartFanMpcProfilesSensor(entry.entry_id, controller, "heat"))
    entities.append(SmartFanMpcProfilesSensor(entry.entry_id, controller, "cool"))
    entities.append(SmartFanLearnedDeadbandSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedSoftErrorSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedHardErrorSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedLimitTimeoutSensor(entry.entry_id, controller))

    data["sensors"] = entities
    async_add_entities(entities)


class SmartFanSensor(_SmartFanEntity):
    """A specific sensor for the Smart Fan integration."""

    def __init__(
        self,
        entry_id: str,
        name_suffix: str,
        data_key: str,
        unit: str | None,
        device_class: SensorDeviceClass | None,
        icon: str,
        entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC,
    ) -> None:
        self._entry_id = entry_id
        self._data_key = data_key
        self._attr_name = name_suffix
        self._attr_unique_id = f"smart_fan_{data_key}_{entry_id}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_native_value = None
        self._attr_icon = icon
        self._attr_entity_category = entity_category

    def update_from_controller(self, data: dict) -> None:
        """Update the sensor value with data from the controller."""
        if self._data_key in data:
            self._attr_native_value = data.get(self._data_key)


class SmartFanLearningSensor(_SmartFanEntity):
    """Sensor showing learning progress and optimal parameters."""

    def __init__(self, entry_id: str, controller) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._attr_name = "Learning Progress"
        self._attr_unique_id = f"smart_fan_learning_progress_{entry_id}"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_icon = "mdi:school"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float:
        """Return learning progress percentage."""
        return round(self._controller.learning.get_progress(), 1)

    @property
    def extra_state_attributes(self) -> dict:
        """Return optimal parameters continuously, even before ready."""
        attrs = {
            "samples_collected": self._controller.learning.slope_sample_count(),
            "response_events": self._controller.learning.response_event_count(),
            "is_ready": self._controller.learning.is_ready(),
            "learned_dead_time": round(self._controller.learning.get_dead_time(), 2),
            "effective_timeout": round(self._controller.get_effective_timeout(), 2),
        }

        optimal = self._controller.learning.compute_optimal_parameters()
        if optimal:
            attrs["learned_deadband"] = optimal.get("deadband")
            attrs["learned_soft_error"] = optimal.get("soft_error")
            attrs["learned_hard_error"] = optimal.get("hard_error")
            attrs["learned_limit_timeout"] = optimal.get("limit_timeout")
            attrs["learned_samples_count"] = optimal.get("samples_count")
            attrs["learned_response_samples"] = optimal.get("response_samples")

        return attrs


class SmartFanLearningStatusSensor(_SmartFanEntity):
    """Sensor showing learning readiness status."""

    def __init__(self, entry_id: str, controller) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._attr_name = "Learning Status"
        self._attr_unique_id = f"smart_fan_learning_status_{entry_id}"
        self._attr_icon = "mdi:school-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str:
        """Return learning status."""
        if self._controller.learning.is_ready():
            return "Ready"
        progress = self._controller.learning.get_progress()
        return f"Learning ({progress:.0f}%)"


class SmartFanLearningSamplesSensor(_SmartFanEntity):
    """Sensor showing number of slope samples collected."""

    def __init__(self, entry_id: str, controller) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._attr_name = "Learning Samples"
        self._attr_unique_id = f"smart_fan_learning_samples_{entry_id}"
        self._attr_native_unit_of_measurement = "samples"
        self._attr_icon = "mdi:chart-box-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int:
        """Return number of samples collected."""
        return self._controller.learning.slope_sample_count()

    @property
    def extra_state_attributes(self) -> dict:
        """Return sample statistics."""
        learning = self._controller.learning
        optimal = learning.compute_optimal_parameters()

        return {
            "min_samples_required": learning.min_samples,
            "slope_mean": round(learning.slope_mean, 3),
            "slope_stdev": round(((learning.slope_m2 / (learning.slope_count - 1)) ** 0.5) if learning.slope_count > 1 else 0, 3),
            "slope_max": round(learning.slope_max, 3),
            "samples_count": optimal.get("samples_count", 0),
        }


class SmartFanLearningResponseSensor(_SmartFanEntity):
    """Sensor showing number of response events recorded."""

    def __init__(self, entry_id: str, controller) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._attr_name = "Learning Response Events"
        self._attr_unique_id = f"smart_fan_learning_response_events_{entry_id}"
        self._attr_native_unit_of_measurement = "events"
        self._attr_icon = "mdi:timer-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int:
        """Return number of response events."""
        return self._controller.learning.response_event_count()

    @property
    def extra_state_attributes(self) -> dict:
        """Return response time statistics."""
        learning = self._controller.learning
        optimal = learning.compute_optimal_parameters()
        response_times = [t for _, t in learning.response_events if t > 0]
        avg_response = sum(response_times) / len(response_times) if response_times else 0

        return {
            "response_samples": optimal.get("response_samples", 0),
            "avg_response_time_min": round(avg_response, 1),
            "median_response_time_min": round(learning.get_dead_time(), 2),
            "effective_timeout_min": round(self._controller.get_effective_timeout(), 2),
            "computed_limit_timeout": optimal.get("limit_timeout", 0),
        }


class SmartFanLearnedDeadTimeSensor(_SmartFanEntity):
    """Sensor showing the learned thermal dead time."""

    def __init__(self, entry_id: str, controller) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._attr_name = "Learned Dead Time"
        self._attr_unique_id = f"smart_fan_learned_dead_time_{entry_id}"
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_icon = "mdi:timer-sand"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float:
        """Return the median learned response delay."""
        return round(self._controller.learning.get_dead_time(), 2)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose readiness context for the learned dead time."""
        return {
            "is_ready": self._controller.learning.is_ready(),
            "response_events": self._controller.learning.response_event_count(),
        }


class SmartFanEffectiveTimeoutSensor(_SmartFanEntity):
    """Sensor showing the actual non-emergency timeout in use."""

    def __init__(self, entry_id: str, controller) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._attr_name = "Effective Timeout"
        self._attr_unique_id = f"smart_fan_effective_timeout_{entry_id}"
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_icon = "mdi:clock-check-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float:
        """Return the actual timeout currently used by controller decisions."""
        return round(self._controller.get_effective_timeout(), 2)

    @property
    def extra_state_attributes(self) -> dict:
        """Show how the effective timeout is derived."""
        return {
            "is_ready": self._controller.learning.is_ready(),
            "learned_dead_time": round(self._controller.learning.get_dead_time(), 2),
            "configured_limit_timeout": round(self._controller.limit_timeout, 2),
        }


class SmartFanMpcProfilesSensor(_SmartFanEntity):
    """Sensor exposing the learned per-mode profiles used by the shadow MPC."""

    def __init__(self, entry_id: str, controller, hvac_mode: str) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._hvac_mode = hvac_mode
        self._attr_name = f"MPC {hvac_mode.title()} Profiles"
        self._attr_unique_id = f"smart_fan_mpc_profiles_{hvac_mode}_{entry_id}"
        self._attr_icon = "mdi:chart-timeline-variant-shimmer"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int:
        """Return the number of reliable profiles currently available."""
        profiles = self._controller.learning.get_mode_profiles(self._hvac_mode, self._controller.fan_modes)
        return sum(1 for profile in profiles.values() if profile["ready"])

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the learned effective slope for each fan mode."""
        profiles = self._controller.learning.get_mode_profiles(self._hvac_mode, self._controller.fan_modes)
        return {
            "hvac_mode": self._hvac_mode,
            "fan_modes_total": len(profiles),
            "known_profiles": sum(1 for profile in profiles.values() if profile["ready"]),
            "min_samples_required_per_profile": MIN_MODE_PROFILE_SAMPLES,
            "profiles": profiles,
        }


class _BaseLearnedParameterSensor(_SmartFanEntity):
    """Base class for learned parameter sensors."""

    def __init__(self, entry_id: str, controller, name: str, unit, device_class, key: str, icon: str = "mdi:brain", current_attr: str | None = None) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._key = key
        self._current_attr = current_attr
        self._attr_name = name
        self._attr_unique_id = f"smart_fan_{self._key}_{entry_id}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._attr_native_value = None
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Return the learned value, or current value if not ready yet."""
        optimal = self._controller.learning.compute_optimal_parameters()
        if optimal:
            return round(optimal.get(self._key), 2)
        if self._current_attr:
            val = getattr(self._controller, f"_{self._current_attr}", 0)
            return round(val, 2) if val else 0
        return 0

    @property
    def extra_state_attributes(self) -> dict:
        """Expose readiness and sample counts for context."""
        learning = self._controller.learning
        return {
            "is_ready": learning.is_ready(),
            "samples_collected": learning.slope_sample_count(),
            "response_events": learning.response_event_count(),
        }


class SmartFanLearnedDeadbandSensor(_BaseLearnedParameterSensor):
    """Learned deadband parameter."""

    def __init__(self, entry_id: str, controller) -> None:
        super().__init__(
            entry_id,
            controller,
            name="Learned Deadband",
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            key="deadband",
            icon="mdi:thermometer-lines",
            current_attr="deadband",
        )


class SmartFanLearnedSoftErrorSensor(_BaseLearnedParameterSensor):
    """Learned soft_error parameter."""

    def __init__(self, entry_id: str, controller) -> None:
        super().__init__(
            entry_id,
            controller,
            name="Learned Soft Error",
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            key="soft_error",
            icon="mdi:speedometer-slow",
            current_attr="soft_error",
        )


class SmartFanLearnedHardErrorSensor(_BaseLearnedParameterSensor):
    """Learned hard_error parameter."""

    def __init__(self, entry_id: str, controller) -> None:
        super().__init__(
            entry_id,
            controller,
            name="Learned Hard Error",
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            key="hard_error",
            icon="mdi:speedometer",
            current_attr="hard_error",
        )


class SmartFanLearnedLimitTimeoutSensor(_BaseLearnedParameterSensor):
    """Learned limit_timeout parameter."""

    def __init__(self, entry_id: str, controller) -> None:
        super().__init__(
            entry_id,
            controller,
            name="Learned Limit Timeout",
            unit=UnitOfTime.MINUTES,
            device_class=SensorDeviceClass.DURATION,
            key="limit_timeout",
            icon="mdi:clock-check-outline",
            current_attr="limit_timeout",
        )
