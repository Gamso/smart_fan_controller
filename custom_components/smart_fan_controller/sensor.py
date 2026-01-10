"""Sensor platform for Smart Fan Controller."""
from __future__ import annotations

import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    climate_id = data["climate_entity"]

    # Define all sensors clearly
    # Format: (Display Name, Data Key, Unit, Device Class)
    sensor_definitions = [
        ("Status", "reason", None, None),
        ("Projected Temperature (10 min)", "temperature_projection", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("Projected Error (10 min)", "projected_temperature_error", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        ("Last change", "minutes_since_last_change", UnitOfTime.MINUTES, SensorDeviceClass.DURATION),
    ]

    entities = []
    for name, key, unit, device_class in sensor_definitions:
        entities.append(
            SmartFanSensor(entry.entry_id, climate_id, name, key, unit, device_class)
        )

    # Store the list in hass.data for the __init__.py update loop
    data["sensors"] = entities
    async_add_entities(entities)


class SmartFanSensor(SensorEntity):
    """A specific sensor for the Smart Fan integration."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry_id, climate_id, name_suffix, data_key, unit, device_class) -> None:
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
