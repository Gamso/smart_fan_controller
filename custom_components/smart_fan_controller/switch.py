"""Switch platform for Smart Fan Controller."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, CONF_LEARNING_ENABLED

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the switch platform from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    controller = data["controller"]

    entities = [
        SmartFanLearningSwitch(entry.entry_id, controller, entry, hass),
    ]

    async_add_entities(entities)


class SmartFanLearningSwitch(SwitchEntity):
    """Switch to enable/disable learning mode."""

    def __init__(self, entry_id: str, controller, entry: ConfigEntry, hass: HomeAssistant) -> None:
        """Initialize the learning switch."""
        self._entry_id = entry_id
        self._controller = controller
        self._entry = entry
        self._hass = hass

        self._attr_name = "Learning Enabled"
        self._attr_unique_id = f"smart_fan_learning_enabled_{entry_id}"
        self._attr_icon = "mdi:brain"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def device_info(self) -> DeviceInfo:
        """Link to the Smart Fan device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Smart Fan Controller",
        )

    @property
    def is_on(self) -> bool:
        """Return true if learning is enabled."""
        return self._controller.learning_enabled

    def turn_on(self, **kwargs) -> None:
        """Turn on learning mode (sync — HA calls async_turn_on when available)."""
        self._controller.learning_enabled = True

    def turn_off(self, **kwargs) -> None:
        """Turn off learning mode (sync — HA calls async_turn_off when available)."""
        self._controller.learning_enabled = False

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on learning mode."""
        self._controller.learning_enabled = True

        # Update config entry to persist the setting
        new_options = {**self._entry.options, CONF_LEARNING_ENABLED: True}
        self._hass.config_entries.async_update_entry(self._entry, options=new_options)

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off learning mode."""
        self._controller.learning_enabled = False

        # Update config entry to persist the setting
        new_options = {**self._entry.options, CONF_LEARNING_ENABLED: False}
        self._hass.config_entries.async_update_entry(self._entry, options=new_options)

        self.async_write_ha_state()
