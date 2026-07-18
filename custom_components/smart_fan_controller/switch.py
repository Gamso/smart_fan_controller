"""Switch platform for Smart Fan Controller."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEVICE_NAME, DOMAIN, build_scoped_entity_id, build_unique_id
from .mpc_controller import MPCController

_LOGGER = logging.getLogger(__name__)


class SmartFanEnvelopeProjectionSwitch(SwitchEntity, RestoreEntity):
    """Toggle the grey-box envelope projection used to rank candidate fan speeds.

    Off by default: an open-loop replay A/B showed it slightly worse than the
    gap-model projection near the setpoint (see the USE_ENVELOPE_PROJECTION
    comment in mpc_controller.py for the numbers). Exposed as a switch instead
    of a code constant so it can be trialled on-device, in closed loop, without
    a restart. The last state is restored across restarts via RestoreEntity.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:function-variant"

    def __init__(self, entry_id: str, climate_entity: str, controller: MPCController) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._attr_name = "Envelope Projection"
        self._attr_unique_id = build_unique_id("use_envelope_projection", entry_id)
        self.entity_id = build_scoped_entity_id("switch", climate_entity, "use_envelope_projection")

    @property
    def device_info(self) -> DeviceInfo:
        """Link the entity to the Smart Fan Controller device."""
        return DeviceInfo(identifiers={(DOMAIN, self._entry_id)}, name=DEVICE_NAME)

    @property
    def is_on(self) -> bool:
        """Return whether the envelope projection is currently used for ranking."""
        return self._controller.use_envelope_projection

    async def async_added_to_hass(self) -> None:
        """Restore the last known state so the choice survives a restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._controller.use_envelope_projection = last_state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Switch fan-speed ranking to the grey-box envelope projection."""
        self._controller.use_envelope_projection = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Switch fan-speed ranking back to the gap-error model."""
        self._controller.use_envelope_projection = False
        self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the switch platform from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    mpc = data["mpc_controller"]
    climate_entity = data["climate_entity"]
    async_add_entities([SmartFanEnvelopeProjectionSwitch(entry.entry_id, climate_entity, mpc)])
