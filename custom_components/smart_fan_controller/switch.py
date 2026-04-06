"""Switch platform for Smart Fan Controller."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_LEARNING_ENABLED,
    CONF_MPC_PRODUCTION_ENABLED,
    DEVICE_NAME,
    DOMAIN,
    build_entity_id,
    build_unique_id,
)

_LOGGER = logging.getLogger(__name__)


class _SmartFanSwitchEntity(SwitchEntity):
    """Base switch wired to the Smart Fan Controller device."""

    _entry_id: str
    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Link the entity to the Smart Fan Controller device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=DEVICE_NAME,
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the switch platform from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    controller = data["controller"]
    mpc_controller = data["mpc_controller"]

    entities = [
        SmartFanLearningSwitch(entry.entry_id, controller, entry, hass),
        SmartFanMpcProductionSwitch(entry.entry_id, mpc_controller, entry, hass),
    ]

    async_add_entities(entities)


class SmartFanLearningSwitch(_SmartFanSwitchEntity):  # pylint: disable=abstract-method
    """Switch to enable or disable learning mode."""

    def __init__(self, entry_id: str, controller, entry: ConfigEntry, hass: HomeAssistant) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._entry = entry
        self._hass = hass

        self._attr_name = "Learning Enabled"
        self._attr_unique_id = build_unique_id("learning_enabled", entry_id)
        self._attr_icon = "mdi:brain"
        self._attr_entity_category = EntityCategory.CONFIG
        self.entity_id = build_entity_id("switch", "learning_enabled")

    @property
    def is_on(self) -> bool:
        """Return true if learning is enabled."""
        return self._controller.learning_enabled

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on learning mode."""
        self._controller.learning_enabled = True
        self._hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_LEARNING_ENABLED: True},
        )
        _LOGGER.info("Learning enabled for %s", self._entry.entry_id)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off learning mode."""
        self._controller.learning_enabled = False
        self._hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_LEARNING_ENABLED: False},
        )
        _LOGGER.info("Learning disabled for %s", self._entry.entry_id)
        self.async_write_ha_state()


class SmartFanMpcProductionSwitch(_SmartFanSwitchEntity):  # pylint: disable=abstract-method
    """Switch to enable MPC production mode (MPC controls the fan)."""

    def __init__(self, entry_id: str, shadow_controller, entry: ConfigEntry, hass: HomeAssistant) -> None:
        self._entry_id = entry_id
        self._shadow_controller = shadow_controller
        self._entry = entry
        self._hass = hass

        self._attr_name = "MPC Production Mode"
        self._attr_unique_id = build_unique_id("mpc_production_mode", entry_id)
        self._attr_icon = "mdi:robot-outline"
        self._attr_entity_category = EntityCategory.CONFIG
        self.entity_id = build_entity_id("switch", "mpc_production_mode")

    @property
    def is_on(self) -> bool:
        """Return true if MPC production mode is enabled."""
        return self._shadow_controller.production_mode

    async def async_turn_on(self, **kwargs) -> None:
        """Enable MPC production mode (MPC controls the fan)."""
        self._shadow_controller.production_mode = True
        self._hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_MPC_PRODUCTION_ENABLED: True},
        )
        _LOGGER.info("MPC production mode enabled for %s", self._entry.entry_id)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Disable MPC production mode (shadow only)."""
        self._shadow_controller.production_mode = False
        self._hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_MPC_PRODUCTION_ENABLED: False},
        )
        _LOGGER.info("MPC production mode disabled for %s", self._entry.entry_id)
        self.async_write_ha_state()
