"""Sensor platform for Smart Fan Controller."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import (
    DEVICE_NAME,
    DOMAIN,
    EFFECTIVE_SLOPE_UNIT,
    MIN_MODE_PROFILE_SAMPLES,
    PROFILE_HVAC_MODES,
    REFERENCE_SLOPE_ERROR,
    build_scoped_entity_id,
    build_unique_id,
)

_LOGGER = logging.getLogger(__name__)


class _SmartFanEntity(SensorEntity):
    """Base sensor wired to the Smart Fan Controller device."""

    _entry_id: str
    _climate_entity: str
    _attr_has_entity_name = True

    def _set_entity_id(self, object_key: str) -> None:
        """Assign the climate-scoped entity_id."""
        self.entity_id = build_scoped_entity_id("sensor", self._climate_entity, object_key)

    @property
    def device_info(self) -> DeviceInfo:
        """Link the entity to the Smart Fan Controller device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=DEVICE_NAME,
        )


def _profile_effective_slope_object_key(hvac_mode: str, fan_mode: str) -> str:
    """Return the canonical object key for a profile effective slope sensor."""
    return f"{hvac_mode}_{slugify(fan_mode)}_effective_slope"


def _build_profile_effective_slope_entities(
    entry_id: str,
    climate_entity: str,
    controller,
    known_keys: set[tuple[str, str]],
) -> list["SmartFanProfileEffectiveSlopeSensor"]:
    """Create profile slope sensors for fan modes that do not yet have an entity."""
    entities: list[SmartFanProfileEffectiveSlopeSensor] = []

    for hvac_mode in PROFILE_HVAC_MODES:
        for fan_mode in controller.fan_modes or []:
            key = (hvac_mode, fan_mode)
            if key in known_keys:
                continue

            known_keys.add(key)
            entities.append(
                SmartFanProfileEffectiveSlopeSensor(
                    entry_id,
                    climate_entity,
                    controller,
                    hvac_mode,
                    fan_mode,
                )
            )

    return entities


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    mpc = data["mpc_controller"]
    climate_entity = data["climate_entity"]

    sensor_definitions = [
        # No ENUM device_class: it requires a static `options` list, but fan modes
        # are discovered at runtime and vary per climate, which would emit HA
        # validation warnings. A plain text sensor shows the fan mode just fine.
        ("Fan Mode", "fan_mode", "fan_mode", None, None, "mdi:fan", None),
        (
            "Fan Mode Last Change",
            "fan_mode_last_change",
            "minutes_since_last_change",
            UnitOfTime.MINUTES,
            SensorDeviceClass.DURATION,
            "mdi:clock-outline",
            EntityCategory.DIAGNOSTIC,
        ),
        ("MPC Status", "mpc_status", "mpc_status", None, None, "mdi:robot-outline", EntityCategory.DIAGNOSTIC),
        ("MPC Reason", "mpc_reason", "mpc_reason", None, None, "mdi:text-box-search-outline", EntityCategory.DIAGNOSTIC),
        ("MPC Fan Mode", "mpc_fan_mode", "mpc_fan_mode", None, None, "mdi:fan-chevron-up", EntityCategory.DIAGNOSTIC),
        ("MPC Would Change Now", "mpc_would_change_now", "mpc_would_change_now", None, None, "mdi:swap-horizontal", EntityCategory.DIAGNOSTIC),
        ("MPC Cost", "mpc_cost", "mpc_cost", None, None, "mdi:calculator", EntityCategory.DIAGNOSTIC),
        ("MPC Confidence", "mpc_confidence", "mpc_confidence", PERCENTAGE, None, "mdi:chart-line", EntityCategory.DIAGNOSTIC),
        ("MPC Predicted Temperature 10 Min", "mpc_predicted_temperature_10_min", "mpc_predicted_temperature_10m", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:chart-timeline-variant", EntityCategory.DIAGNOSTIC),
        ("MPC Predicted Temperature 30 Min", "mpc_predicted_temperature_30_min", "mpc_predicted_temperature_30m", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, "mdi:chart-timeline-variant", EntityCategory.DIAGNOSTIC),
        ("MPC Dead Time", "mpc_dead_time", "mpc_dead_time", UnitOfTime.MINUTES, SensorDeviceClass.DURATION, "mdi:timer-sand", EntityCategory.DIAGNOSTIC),
        ("MPC Known Profiles", "mpc_known_profiles", "mpc_known_profiles", None, None, "mdi:database-search-outline", EntityCategory.DIAGNOSTIC),
        ("MPC Disturbance Bias", "mpc_disturbance_bias", "mpc_disturbance_bias", EFFECTIVE_SLOPE_UNIT, None, "mdi:weather-windy", EntityCategory.DIAGNOSTIC),
    ]

    entities: list[SensorEntity] = [
        SmartFanSensor(
            entry.entry_id,
            climate_entity,
            name,
            object_key,
            controller_key,
            unit,
            device_class,
            icon,
            entity_category,
        )
        for name, object_key, controller_key, unit, device_class, icon, entity_category in sensor_definitions
    ]

    entities.extend(
        [
            SmartFanLearningSensor(entry.entry_id, climate_entity, mpc),
            SmartFanLearningStatusSensor(entry.entry_id, climate_entity, mpc),
            SmartFanLearningSamplesSensor(entry.entry_id, climate_entity, mpc),
            SmartFanLearningResponseSensor(entry.entry_id, climate_entity, mpc),
            SmartFanLearnedDeadTimeSensor(entry.entry_id, climate_entity, mpc),
            SmartFanEffectiveTimeoutSensor(entry.entry_id, climate_entity, mpc),
            SmartFanMpcProfilesSensor(entry.entry_id, climate_entity, mpc, "heat"),
            SmartFanMpcProfilesSensor(entry.entry_id, climate_entity, mpc, "cool"),
            SmartFanLearnedDeadbandSensor(entry.entry_id, climate_entity, mpc),
        ]
    )

    profile_sensor_keys: set[tuple[str, str]] = set()
    profile_entities = _build_profile_effective_slope_entities(
        entry.entry_id,
        climate_entity,
        mpc,
        profile_sensor_keys,
    )
    entities.extend(profile_entities)

    if profile_entities:
        _LOGGER.info(
            "Created %d initial effective slope profile sensors for %s: %s",
            len(profile_entities),
            entry.entry_id,
            [entity.name for entity in profile_entities],
        )
    else:
        _LOGGER.debug("No fan modes known yet for %s; profile slope sensors will be added later", entry.entry_id)

    data["profile_sensor_keys"] = profile_sensor_keys
    data["sensors"] = entities

    def ensure_profile_sensors() -> None:
        """Add late-discovered profile slope sensors once fan modes are known."""
        new_entities = _build_profile_effective_slope_entities(
            entry.entry_id,
            climate_entity,
            mpc,
            profile_sensor_keys,
        )
        if not new_entities:
            return

        entities.extend(new_entities)
        async_add_entities(new_entities)
        _LOGGER.info(
            "Added %d effective slope profile sensors for %s: %s",
            len(new_entities),
            entry.entry_id,
            [entity.name for entity in new_entities],
        )

    data["ensure_profile_sensors"] = ensure_profile_sensors

    async_add_entities(entities)


class SmartFanSensor(_SmartFanEntity):
    """A specific sensor fed by the live controller payload."""

    def __init__(
        self,
        entry_id: str,
        climate_entity: str,
        name_suffix: str,
        object_key: str,
        data_key: str,
        unit: str | None,
        device_class: SensorDeviceClass | None,
        icon: str,
        entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC,
    ) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._data_key = data_key
        self._attr_name = name_suffix
        self._attr_unique_id = build_unique_id(object_key, entry_id)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_native_value = None
        self._attr_icon = icon
        self._attr_entity_category = entity_category
        self._set_entity_id(object_key)

    def update_from_mpc(self, data: dict) -> None:
        """Update the sensor value with data from the controller."""
        if self._data_key in data:
            self._attr_native_value = data.get(self._data_key)


class SmartFanLearningSensor(_SmartFanEntity):
    """Sensor showing learning progress and optimal parameters."""

    def __init__(self, entry_id: str, climate_entity: str, controller) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._attr_name = "Learning Progress"
        self._attr_unique_id = build_unique_id("learning_progress", entry_id)
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_icon = "mdi:school"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id("learning_progress")

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
            attrs["learned_samples_count"] = optimal.get("samples_count")
            attrs["learned_response_samples"] = optimal.get("response_samples")

        return attrs


class SmartFanLearningStatusSensor(_SmartFanEntity):
    """Sensor showing learning readiness status."""

    def __init__(self, entry_id: str, climate_entity: str, controller) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._attr_name = "Learning Status"
        self._attr_unique_id = build_unique_id("learning_status", entry_id)
        self._attr_icon = "mdi:school-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id("learning_status")

    @property
    def native_value(self) -> str:
        """Return learning status."""
        if self._controller.learning.is_ready():
            return "Ready"
        progress = self._controller.learning.get_progress()
        return f"Learning ({progress:.0f}%)"


class SmartFanLearningSamplesSensor(_SmartFanEntity):
    """Sensor showing number of slope samples collected."""

    def __init__(self, entry_id: str, climate_entity: str, controller) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._attr_name = "Learning Samples"
        self._attr_unique_id = build_unique_id("learning_samples", entry_id)
        self._attr_native_unit_of_measurement = "samples"
        self._attr_icon = "mdi:chart-box-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id("learning_samples")

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

    def __init__(self, entry_id: str, climate_entity: str, controller) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._attr_name = "Learning Response Events"
        self._attr_unique_id = build_unique_id("learning_response_events", entry_id)
        self._attr_native_unit_of_measurement = "events"
        self._attr_icon = "mdi:timer-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id("learning_response_events")

    @property
    def native_value(self) -> int:
        """Return number of response events."""
        return self._controller.learning.response_event_count()

    @property
    def extra_state_attributes(self) -> dict:
        """Return response time statistics."""
        learning = self._controller.learning
        optimal = learning.compute_optimal_parameters()
        response_times = [item[1] for item in learning.response_events if item[1] > 0]
        avg_response = sum(response_times) / len(response_times) if response_times else 0

        return {
            "response_samples": optimal.get("response_samples", 0),
            "avg_response_time_min": round(avg_response, 1),
            "median_response_time_min": round(learning.get_dead_time(), 2),
            "effective_timeout_min": round(self._controller.get_effective_timeout(), 2),
        }


class SmartFanLearnedDeadTimeSensor(_SmartFanEntity):
    """Sensor showing the learned thermal dead time."""

    def __init__(self, entry_id: str, climate_entity: str, controller) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._attr_name = "Learned Dead Time"
        self._attr_unique_id = build_unique_id("learned_dead_time", entry_id)
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_icon = "mdi:timer-sand"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id("learned_dead_time")

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

    def __init__(self, entry_id: str, climate_entity: str, controller) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._attr_name = "Effective Timeout"
        self._attr_unique_id = build_unique_id("effective_timeout", entry_id)
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_icon = "mdi:clock-check-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id("effective_timeout")

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
        }


class SmartFanMpcProfilesSensor(_SmartFanEntity):
    """Sensor exposing the learned per-mode profiles used by the MPC."""

    def __init__(self, entry_id: str, climate_entity: str, controller, hvac_mode: str) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._hvac_mode = hvac_mode
        self._attr_name = f"MPC {hvac_mode.title()} Profiles"
        self._attr_unique_id = build_unique_id(f"mpc_{hvac_mode}_profiles", entry_id)
        self._attr_icon = "mdi:chart-timeline-variant-shimmer"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id(f"mpc_{hvac_mode}_profiles")

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
            # Strength order actually in use (weakest first). Load-bearing: it drives
            # the energy cost, the step-down guard and the unlearned-profile fallback,
            # so it is surfaced here to be verifiable without reading the logs.
            "fan_mode_order": list(self._controller.fan_modes or []),
            # Learned slopes after ordering violations are resolved in favour of the
            # better-sampled profile; differs from each profile's raw value when a
            # thin estimate had to be clamped.
            "effective_slopes_used": {
                fan_mode: round(value, 3)
                for fan_mode, value in self._controller.build_monotone_slopes(
                    self._controller.fan_modes or [], self._hvac_mode
                ).items()
            },
            "profile_effective_slope_sensors": {
                fan_mode: build_scoped_entity_id(
                    "sensor",
                    self._climate_entity,
                    _profile_effective_slope_object_key(self._hvac_mode, fan_mode),
                )
                for fan_mode in profiles
            },
            "profiles": profiles,
        }


class SmartFanProfileEffectiveSlopeSensor(_SmartFanEntity):
    """Historized per-profile effective slope sensor for one HVAC/fan combination."""

    def __init__(
        self,
        entry_id: str,
        climate_entity: str,
        controller,
        hvac_mode: str,
        fan_mode: str,
    ) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._hvac_mode = hvac_mode
        self._fan_mode = fan_mode

        object_key = _profile_effective_slope_object_key(hvac_mode, fan_mode)
        self._attr_name = f"{hvac_mode.title()} {fan_mode.title()} Effective Slope"
        self._attr_unique_id = build_unique_id(object_key, entry_id)
        self._attr_native_unit_of_measurement = EFFECTIVE_SLOPE_UNIT
        self._attr_icon = "mdi:chart-line-variant"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id(object_key)

    @property
    def native_value(self) -> float | None:
        """Return the learned effective slope for this profile."""
        effective_slope = self._controller.learning.get_mode_effective_slope(self._fan_mode, self._hvac_mode)
        return round(effective_slope, 3) if effective_slope is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose sampling details for the current profile."""
        learning = self._controller.learning
        samples = learning.get_mode_sample_count(self._fan_mode, self._hvac_mode)
        spread = learning.get_profile_spread(self._fan_mode, self._hvac_mode)
        if spread is None:
            quality = "unknown"
        elif spread < 0.15:
            quality = "good"
        elif spread < 0.30:
            quality = "fair"
        else:
            quality = "poor"
        # Gap-dependent slope model: effective_slope(error) = intercept + gain·error.
        # The state value is this model evaluated at REFERENCE_SLOPE_ERROR.
        model = learning.get_mode_slope_model(self._fan_mode, self._hvac_mode)
        if model is None:
            slope_intercept = None
            slope_gain = None
        else:
            slope_intercept = round(model[0], 3)
            slope_gain = round(model[1], 3)
        r_squared = learning.get_mode_slope_r2(self._fan_mode, self._hvac_mode)
        time_constant = learning.get_mode_time_constant(self._fan_mode, self._hvac_mode)
        return {
            "hvac_mode": self._hvac_mode,
            "fan_mode": self._fan_mode,
            "samples": samples,
            "min_samples_required": MIN_MODE_PROFILE_SAMPLES,
            "ready": samples >= MIN_MODE_PROFILE_SAMPLES,
            "spread": spread,
            "quality": quality,
            "slope_intercept": slope_intercept,
            "slope_gain": slope_gain,
            "reference_error": REFERENCE_SLOPE_ERROR,
            "model_r_squared": round(r_squared, 3) if r_squared is not None else None,
            "thermal_time_constant_h": round(time_constant, 2) if time_constant is not None else None,
        }


class _BaseLearnedParameterSensor(_SmartFanEntity):
    """Base class for learned parameter sensors."""

    def __init__(
        self,
        entry_id: str,
        climate_entity: str,
        controller,
        *,
        name: str,
        object_key: str,
        unit,
        device_class,
        learning_key: str,
        icon: str = "mdi:brain",
        current_attr: str | None = None,
    ) -> None:
        self._entry_id = entry_id
        self._climate_entity = climate_entity
        self._controller = controller
        self._learning_key = learning_key
        self._current_attr = current_attr
        self._attr_name = name
        self._attr_unique_id = build_unique_id(object_key, entry_id)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._attr_native_value = None
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._set_entity_id(object_key)

    @property
    def native_value(self):
        """Return the learned value, or current value if not ready yet."""
        optimal = self._controller.learning.compute_optimal_parameters()
        if optimal:
            value = optimal.get(self._learning_key)
            return round(value, 2) if value is not None else None

        if self._current_attr:
            value = getattr(self._controller, f"_{self._current_attr}", 0)
            return round(value, 2) if value else 0

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

    def __init__(self, entry_id: str, climate_entity: str, controller) -> None:
        super().__init__(
            entry_id,
            climate_entity,
            controller,
            name="Learned Deadband",
            object_key="learned_deadband",
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            learning_key="deadband",
            icon="mdi:thermometer-lines",
            current_attr="deadband",
        )
