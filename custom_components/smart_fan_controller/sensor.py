"""Sensor platform for Smart Fan Controller."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime, PERCENTAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]

    # Define all sensors clearly
    # Format: (Display Name, Data Key, Unit, Device Class, Icon, Entity Category)
    sensor_definitions = [
        ("Status", "reason", None, None, "mdi:information-outline", EntityCategory.DIAGNOSTIC),
        ("Fan Mode", "fan_mode", None, SensorDeviceClass.ENUM, "mdi:fan", None),  # Not diagnostic
        ("Fan Mode - Last change", "minutes_since_last_change", UnitOfTime.MINUTES, SensorDeviceClass.DURATION, "mdi:clock-outline", EntityCategory.DIAGNOSTIC),
        ("Temperature Projected (10 min)", "projected_temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:chart-bell-curve", EntityCategory.DIAGNOSTIC),
        ("Temperature Projected Error (10 min)", "projected_temperature_error", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:chart-bell-curve", EntityCategory.DIAGNOSTIC),
        ("Temperature Error", "temperature_error", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:thermometer-lines", EntityCategory.DIAGNOSTIC),
    ]

    entities = []
    for name, key, unit, device_class, icon, entity_category in sensor_definitions:
        entities.append(SmartFanSensor(entry.entry_id, name, key, unit, device_class, icon, entity_category))

    # Add learning sensors (directly linked to controller)
    controller = data["controller"]
    entities.append(SmartFanLearningSensor(entry.entry_id, controller))
    entities.append(SmartFanLearningStatusSensor(entry.entry_id, controller))
    entities.append(SmartFanLearningSamplesSensor(entry.entry_id, controller))
    entities.append(SmartFanLearningResponseSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedDeadbandSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedSoftErrorSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedHardErrorSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedProjectedErrorSensor(entry.entry_id, controller))
    entities.append(SmartFanLearnedLimitTimeoutSensor(entry.entry_id, controller))

    # Store the list in hass.data for the __init__.py update loop
    data["sensors"] = entities
    async_add_entities(entities)


class SmartFanSensor(SensorEntity):
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
        """Initialize the sensor."""
        self._entry_id = entry_id
        self._data_key = data_key

        # This name is what appears in the UI
        self._attr_name = name_suffix

        # This ID is what appears in developer-tools/state
        # We force the format: sensor.smart_fan_projected_error
        self.entity_id = f"sensor.smart_fan_{data_key}"

        # Unique ID for internal HA database
        self._attr_unique_id = f"smart_fan_{data_key}_{entry_id}"

        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_native_value = None
        self._attr_icon = icon
        self._attr_entity_category = entity_category

    @property
    def device_info(self) -> DeviceInfo:
        """Link the sensor to the main Smart Fan device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Smart Fan Controller",
        )

    def update_from_controller(self, data: dict) -> None:
        """Update the sensor value with data from the controller."""
        new_value = data.get(self._data_key)
        if new_value is not None:
            self._attr_native_value = new_value
            self.async_write_ha_state()


class SmartFanLearningSensor(SensorEntity):
    """Sensor showing learning progress and optimal parameters."""

    def __init__(self, entry_id: str, controller) -> None:
        """Initialize the learning sensor."""
        self._entry_id = entry_id
        self._controller = controller

        self._attr_name = "Learning Progress"
        self.entity_id = "sensor.smart_fan_learning_progress"
        self._attr_unique_id = f"smart_fan_learning_progress_{entry_id}"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_icon = "mdi:school"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        """Link to the Smart Fan device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Smart Fan Controller",
        )

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
        }

        # Always compute optimal parameters for continuous monitoring (not applied automatically)
        optimal = self._controller.learning.compute_optimal_parameters()
        if optimal:
            attrs["learned_deadband"] = optimal.get("deadband")
            attrs["learned_soft_error"] = optimal.get("soft_error")
            attrs["learned_hard_error"] = optimal.get("hard_error")
            attrs["learned_projected_error"] = optimal.get("projected_error_threshold")
            attrs["learned_limit_timeout"] = optimal.get("limit_timeout")
            attrs["learned_samples_count"] = optimal.get("samples_count")
            attrs["learned_response_samples"] = optimal.get("response_samples")

        return attrs

class SmartFanLearningStatusSensor(SensorEntity):
    """Sensor showing learning readiness status."""

    def __init__(self, entry_id: str, controller) -> None:
        """Initialize the learning status sensor."""
        self._entry_id = entry_id
        self._controller = controller

        self._attr_name = "Learning Status"
        self.entity_id = "sensor.smart_fan_learning_status"
        self._attr_unique_id = f"smart_fan_learning_status_{entry_id}"
        self._attr_icon = "mdi:school-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        """Link to the Smart Fan device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Smart Fan Controller",
        )

    @property
    def native_value(self) -> str:
        """Return learning status."""
        if self._controller.learning.is_ready():
            return "Ready"
        else:
            progress = self._controller.learning.get_progress()
            return f"Learning ({progress:.0f}%)"


class SmartFanLearningSamplesSensor(SensorEntity):
    """Sensor showing number of slope samples collected."""

    def __init__(self, entry_id: str, controller) -> None:
        """Initialize the samples sensor."""
        self._entry_id = entry_id
        self._controller = controller

        self._attr_name = "Learning Samples"
        self.entity_id = "sensor.smart_fan_learning_samples"
        self._attr_unique_id = f"smart_fan_learning_samples_{entry_id}"
        self._attr_native_unit_of_measurement = "samples"
        self._attr_icon = "mdi:chart-box-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        """Link to the Smart Fan device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Smart Fan Controller",
        )

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
            "min_samples_required": learning._min_samples,
            "slope_mean": round(learning._slope_mean, 3),
            "slope_stdev": round(((learning._slope_M2 / (learning._slope_count - 1)) ** 0.5) if learning._slope_count > 1 else 0, 3),
            "slope_max": round(learning._slope_max, 3),
            "samples_count": optimal.get("samples_count", 0),
        }


class SmartFanLearningResponseSensor(SensorEntity):
    """Sensor showing number of response events recorded."""

    def __init__(self, entry_id: str, controller) -> None:
        """Initialize the response events sensor."""
        self._entry_id = entry_id
        self._controller = controller

        self._attr_name = "Learning Response Events"
        self.entity_id = "sensor.smart_fan_learning_response_events"
        self._attr_unique_id = f"smart_fan_learning_response_events_{entry_id}"
        self._attr_native_unit_of_measurement = "events"
        self._attr_icon = "mdi:timer-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        """Link to the Smart Fan device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Smart Fan Controller",
        )

    @property
    def native_value(self) -> int:
        """Return number of response events."""
        return self._controller.learning.response_event_count()

    @property
    def extra_state_attributes(self) -> dict:
        """Return response time statistics."""
        learning = self._controller.learning
        optimal = learning.compute_optimal_parameters()

        response_times = [t for _, t in learning._response_events if t > 0]
        avg_response = sum(response_times) / len(response_times) if response_times else 0

        return {
            "response_samples": optimal.get("response_samples", 0),
            "avg_response_time_min": round(avg_response, 1),
            "computed_limit_timeout": optimal.get("limit_timeout", 0),
        }


class _BaseLearnedParameterSensor(SensorEntity):
    """Base class for learned parameter sensors."""

    def __init__(self, entry_id: str, controller, name: str, entity_id: str, unit, device_class, key: str, icon: str = "mdi:brain", current_attr: str | None = None) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._key = key
        self._current_attr = current_attr  # Attribute to fetch current value from controller

        self._attr_name = name
        self.entity_id = entity_id
        self._attr_unique_id = f"smart_fan_{self._key}_{entry_id}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._attr_native_value = None  # Initialize with None, will be updated
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        """Link to the Smart Fan device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Smart Fan Controller",
        )

    @property
    def native_value(self):
        """Return the learned value, or current value if not ready yet."""
        optimal = self._controller.learning.compute_optimal_parameters()
        if optimal:
            return round(optimal.get(self._key), 2)
        # Before learning is ready, show current value from controller if available
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
            entity_id="sensor.smart_fan_learned_deadband",
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
            entity_id="sensor.smart_fan_learned_soft_error",
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            key="soft_error",
            icon="mdi:alert-circle-outline",
            current_attr="soft_error",
        )


class SmartFanLearnedHardErrorSensor(_BaseLearnedParameterSensor):
    """Learned hard_error parameter."""

    def __init__(self, entry_id: str, controller) -> None:
        super().__init__(
            entry_id,
            controller,
            name="Learned Hard Error",
            entity_id="sensor.smart_fan_learned_hard_error",
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            key="hard_error",
            icon="mdi:alert",
            current_attr="hard_error",
        )


class SmartFanLearnedProjectedErrorSensor(_BaseLearnedParameterSensor):
    """Learned projected_error_threshold parameter."""

    def __init__(self, entry_id: str, controller) -> None:
        super().__init__(
            entry_id,
            controller,
            name="Learned Projected Error",
            entity_id="sensor.smart_fan_learned_projected_error",
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            key="projected_error_threshold",
            icon="mdi:crystal-ball",
            current_attr="projected_error_threshold",
        )


class SmartFanLearnedLimitTimeoutSensor(_BaseLearnedParameterSensor):
    """Learned limit_timeout parameter."""

    def __init__(self, entry_id: str, controller) -> None:
        super().__init__(
            entry_id,
            controller,
            name="Learned Limit Timeout",
            entity_id="sensor.smart_fan_learned_limit_timeout",
            unit=UnitOfTime.MINUTES,
            device_class=SensorDeviceClass.DURATION,
            key="limit_timeout",
            icon="mdi:clock-check-outline",
            current_attr="limit_timeout",
        )
