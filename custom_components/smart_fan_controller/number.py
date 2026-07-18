"""Number platform for Smart Fan Controller — per-fan-speed rated airflow input."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DEVICE_NAME, DOMAIN, build_scoped_entity_id, build_unique_id

_LOGGER = logging.getLogger(__name__)


def _airflow_object_key(fan_mode: str) -> str:
    """Return the canonical object key for a fan-speed airflow number entity."""
    return f"{slugify(fan_mode)}_airflow"


def _build_airflow_entities(
    entry_id: str,
    climate_entity: str,
    controller,
    known_keys: set[str],
) -> list["SmartFanAirflowNumber"]:
    """Create airflow number entities for fan modes that do not yet have one."""
    entities: list[SmartFanAirflowNumber] = []

    for fan_mode in controller.fan_modes or []:
        if fan_mode in known_keys:
            continue

        known_keys.add(fan_mode)
        entities.append(SmartFanAirflowNumber(entry_id, climate_entity, controller, fan_mode))

    return entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the number platform from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    mpc = data["mpc_controller"]
    climate_entity = data["climate_entity"]

    airflow_number_keys: set[str] = set()
    entities = _build_airflow_entities(entry.entry_id, climate_entity, mpc, airflow_number_keys)
    data["airflow_number_keys"] = airflow_number_keys

    def ensure_airflow_numbers() -> None:
        """Add late-discovered airflow number entities once fan modes are known."""
        new_entities = _build_airflow_entities(entry.entry_id, climate_entity, mpc, airflow_number_keys)
        if not new_entities:
            return

        async_add_entities(new_entities)
        _LOGGER.info(
            "Added %d airflow number entities for %s: %s",
            len(new_entities),
            entry.entry_id,
            [entity.name for entity in new_entities],
        )

    data["ensure_airflow_numbers"] = ensure_airflow_numbers

    async_add_entities(entities)


class SmartFanAirflowNumber(NumberEntity):
    """User-entered rated airflow (m3/h) for one fan speed.

    Purely optional hardware spec, not learned data. When filled in for every
    fan speed seen by the envelope model, it constrains the grey-box fit to
    u_fan = a + b·airflow instead of an independent value per fan — so weak
    modes with few or no envelope samples of their own still get a
    physically-grounded power estimate from whatever data the other fan
    speeds provide. See thermal_learning.py's _envelope_diagnostics.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
    _attr_native_min_value = 0.0
    _attr_native_max_value = 5000.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:fan"

    def __init__(self, entry_id: str, climate_entity: str, controller, fan_mode: str) -> None:
        self._entry_id = entry_id
        self._controller = controller
        self._fan_mode = fan_mode
        object_key = _airflow_object_key(fan_mode)
        self._attr_name = f"{fan_mode.title()} Airflow"
        self._attr_unique_id = build_unique_id(object_key, entry_id)
        self.entity_id = build_scoped_entity_id("number", climate_entity, object_key)

    @property
    def device_info(self) -> DeviceInfo:
        """Link the entity to the Smart Fan Controller device."""
        return DeviceInfo(identifiers={(DOMAIN, self._entry_id)}, name=DEVICE_NAME)

    @property
    def native_value(self) -> float | None:
        """Return the rated airflow for this fan speed, or None until set."""
        return self._controller.learning.get_airflow(self._fan_mode)

    async def async_set_native_value(self, value: float) -> None:
        """Store the rated airflow for this fan speed."""
        self._controller.learning.set_airflow(self._fan_mode, value)
        self.async_write_ha_state()
