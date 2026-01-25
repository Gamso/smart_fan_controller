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

    # Add learning progress sensor (directly linked to controller)
    controller = data["controller"]
    entities.append(SmartFanLearningSensor(entry.entry_id, controller))

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
        """Return optimal parameters when ready."""
        attrs = {
            "samples_collected": self._controller.learning.slope_sample_count(),
            "response_events": self._controller.learning.response_event_count(),
            "is_ready": self._controller.learning.is_ready(),
        }

        if self._controller.learning.is_ready():
            optimal = self._controller.learning.compute_optimal_parameters()
            attrs["optimal_parameters"] = optimal

        return attrs
