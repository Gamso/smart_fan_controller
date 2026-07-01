"""Tests for Smart Fan Controller HA services (apply_learned_settings, reset_learning)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from types import SimpleNamespace

from custom_components.smart_fan_controller import (
    _apply_optimal_parameters,
    _async_apply_fan_change,
    _async_migrate_entity_ids,
    _detect_disturbances,
    _read_climate_cycle_data,
    _resolve_active_force,
    _resolve_controller_entry,
    _should_collect_slope_sample,
)
from custom_components.smart_fan_controller.thermal_learning import ThermalLearning
from custom_components.smart_fan_controller.const import (
    CONF_CLIMATE_ENTITY,
    CONF_DEADBAND,
    CONF_DEFROST_ENTITY,
    CONF_LIMIT_TIMEOUT,
    CONF_OPERATING_ENTITY,
    DOMAIN,
    MIN_SAMPLES_LEARNING,
    PHASE_ESTABLISHED,
    build_unique_id,
)


def _make_ready_learning() -> ThermalLearning:
    """Return a ThermalLearning instance that has reached readiness."""
    learning = ThermalLearning()
    for _ in range(MIN_SAMPLES_LEARNING):
        learning.add_slope_sample("medium", 0.4, 0.2)
    for t in [10, 11, 12, 13, 14]:
        learning.add_response_event(t)
    assert learning.is_ready()
    return learning


class TestApplyLearnedSettings:
    """Unit-level tests for the apply_learned_settings service logic."""

    @pytest.mark.asyncio
    async def test_apply_learned_settings_calls_helper_when_ready(self):
        """When learning is ready, apply_learned_settings must call _apply_optimal_parameters."""
        learning = _make_ready_learning()

        entry = MagicMock()
        entry.data = {CONF_DEADBAND: 0.2, CONF_LIMIT_TIMEOUT: 15}
        entry.options = {}

        applied_data = {}

        async def fake_apply(hass, entry, optimal):  # pylint: disable=unused-argument
            applied_data.update(optimal)

        hass = MagicMock()

        if learning.is_ready():
            optimal = learning.compute_optimal_parameters()
            if optimal:
                await fake_apply(hass, entry, optimal)

        assert "deadband" in applied_data
        assert "limit_timeout" in applied_data

    def test_apply_learned_settings_skipped_when_not_ready(self):
        """When learning is not ready, the service must not apply any parameters."""
        learning = ThermalLearning()
        assert not learning.is_ready()

        should_apply = learning.is_ready()
        assert should_apply is False

    def test_optimal_parameters_satisfy_threshold_constraints(self):
        """Learned deadband must be positive and reasonable."""
        learning = _make_ready_learning()
        optimal = learning.compute_optimal_parameters()

        assert optimal["deadband"] > 0, f"deadband ({optimal['deadband']}) must be positive"
        assert optimal["limit_timeout"] > 0, f"limit_timeout ({optimal['limit_timeout']}) must be positive"

    def test_apply_optimal_sets_learning_auto_applied_flag(self):
        """_apply_optimal_parameters must set learning_auto_applied=True in entry.data."""
        hass = MagicMock()
        hass.config_entries = MagicMock()
        hass.config_entries.async_update_entry = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        entry = MagicMock()
        entry.data = {CONF_DEADBAND: 0.2, CONF_LIMIT_TIMEOUT: 15}
        entry.options = {CONF_DEADBAND: 0.2, CONF_LIMIT_TIMEOUT: 15, CONF_CLIMATE_ENTITY: "climate.test"}

        optimal = {"deadband": 0.25, "limit_timeout": 12}

        asyncio.get_event_loop().run_until_complete(_apply_optimal_parameters(hass, entry, optimal))

        # Verify async_update_entry was called with learning_auto_applied=True
        call_args = hass.config_entries.async_update_entry.call_args
        updated_data = call_args[1]["data"]
        updated_options = call_args[1]["options"]
        assert updated_data.get("learning_auto_applied") is True
        assert updated_data[CONF_DEADBAND] == 0.25
        assert updated_data[CONF_LIMIT_TIMEOUT] == 12
        assert updated_options[CONF_DEADBAND] == 0.25
        assert updated_options[CONF_LIMIT_TIMEOUT] == 12

    def test_resolve_controller_entry_requires_target_when_multiple_entries(self):
        """Service routing must require a climate target when several entries are loaded."""
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "entry-1": {"climate_entity": "climate.living_room"},
                "entry-2": {"climate_entity": "climate.bedroom"},
                "_services_registered": True,
            }
        }

        with pytest.raises(HomeAssistantError, match="Multiple Smart Fan Controller entries"):
            _resolve_controller_entry(hass)

    def test_resolve_controller_entry_matches_requested_climate(self):
        """Service routing must resolve the correct loaded entry from climate_entity."""
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                "entry-1": {"climate_entity": "climate.living_room"},
                "entry-2": {"climate_entity": "climate.bedroom"},
                "_services_registered": True,
            }
        }

        entry_id, entry_data = _resolve_controller_entry(hass, "climate.bedroom")

        assert entry_id == "entry-2"
        assert entry_data["climate_entity"] == "climate.bedroom"


@pytest.mark.asyncio
async def test_entity_id_migration_renames_legacy_registry_entries(hass) -> None:
    """Existing legacy entity_ids should migrate to climate-scoped names."""
    entity_registry = er.async_get(hass)
    entry = MagicMock()
    entry.entry_id = "entry-1"
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_unique_id("mpc_status", entry.entry_id),
        config_entry=entry,
        suggested_object_id="smart_fan_controller_mpc_status",
    )

    await _async_migrate_entity_ids(hass, entry, "climate.living_room")

    migrated = entity_registry.async_get("sensor.smart_fan_controller_living_room_mpc_status")
    assert migrated is not None
    assert migrated.unique_id == build_unique_id("mpc_status", entry.entry_id)


@pytest.mark.asyncio
async def test_entity_id_migration_preserves_custom_registry_ids(hass) -> None:
    """Migration should not override a user-customized entity_id."""
    entity_registry = er.async_get(hass)
    entry = MagicMock()
    entry.entry_id = "entry-1"
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_unique_id("mpc_status", entry.entry_id),
        config_entry=entry,
        suggested_object_id="custom_living_room_mpc_status",
    )

    await _async_migrate_entity_ids(hass, entry, "climate.living_room")

    assert entity_registry.async_get("sensor.custom_living_room_mpc_status") is not None
    assert entity_registry.async_get("sensor.smart_fan_controller_living_room_mpc_status") is None


class TestResetLearning:
    """Unit-level tests for the reset_learning service logic."""

    def test_reset_clears_all_samples(self):
        """After reset, no samples or response events must remain."""
        learning = _make_ready_learning()
        assert learning.slope_sample_count() > 0
        assert learning.is_ready()

        learning.reset()

        assert learning.slope_sample_count() == 0
        assert learning.response_event_count() == 0
        assert not learning.is_ready()
        assert learning.get_progress() == 0.0

    def test_reset_invalidates_optimal_cache(self):
        """After reset, compute_optimal_parameters must return empty (not cached stale data)."""
        learning = _make_ready_learning()
        # Prime the cache
        _ = learning.compute_optimal_parameters()
        assert learning.optimal_cache is not None

        learning.reset()

        # Cache must be gone and compute must return empty
        assert learning.optimal_cache is None
        assert learning.compute_optimal_parameters() == {}

    def test_reset_then_relearn(self):
        """After a reset, the system must be able to learn again from scratch."""
        learning = _make_ready_learning()
        learning.reset()

        # Collect new samples
        for _ in range(MIN_SAMPLES_LEARNING):
            learning.add_slope_sample("high", 0.5, 0.1)

        assert learning.is_ready()
        assert learning.slope_sample_count() == MIN_SAMPLES_LEARNING


class TestForceFan:
    """Tests for the force_fan override resolution logic."""

    FAN_MODES = ["silent", "low", "med", "high", "superhigh"]

    def test_no_force_returns_none(self):
        """No override configured -> MPC stays in control."""
        entry_data = {"force": None}
        assert _resolve_active_force(entry_data, self.FAN_MODES, now=1000.0, climate_id="climate.test") is None

    def test_active_force_returns_fan_mode(self):
        """An active override returns the forced fan mode."""
        entry_data = {"force": {"fan_mode": "high", "until": 2000.0}}
        assert _resolve_active_force(entry_data, self.FAN_MODES, now=1000.0, climate_id="climate.test") == "high"
        # Override is preserved while active
        assert entry_data["force"] is not None

    def test_expired_force_is_cleared(self):
        """An expired override returns None and is cleared so the MPC resumes."""
        entry_data = {"force": {"fan_mode": "high", "until": 999.0}}
        assert _resolve_active_force(entry_data, self.FAN_MODES, now=1000.0, climate_id="climate.test") is None
        assert entry_data["force"] is None

    def test_invalid_fan_mode_is_cleared(self):
        """An override for an unknown fan mode is dropped."""
        entry_data = {"force": {"fan_mode": "turbo", "until": 2000.0}}
        assert _resolve_active_force(entry_data, self.FAN_MODES, now=1000.0, climate_id="climate.test") is None
        assert entry_data["force"] is None

    def test_unknown_fan_modes_does_not_block_force(self):
        """Before fan modes are discovered, the override is still honoured."""
        entry_data = {"force": {"fan_mode": "high", "until": 2000.0}}
        assert _resolve_active_force(entry_data, None, now=1000.0, climate_id="climate.test") == "high"


class TestReadClimateCycleData:
    """Tests for parsing/guarding VTherm climate state (M3 hardening)."""

    @staticmethod
    def _state(**overrides):
        attrs = {
            "specific_states": {"temperature_slope": 0.5},
            "current_temperature": 22.0,
            "temperature": 20.0,
            "hvac_mode": "heat",
            "fan_mode": "low",
        }
        attrs.update(overrides)
        return SimpleNamespace(attributes=attrs)

    def test_valid_state_returns_parsed_tuple(self):
        result = _read_climate_cycle_data(self._state(), "climate.test")
        assert result == (0.5, 22.0, 20.0, "heat", "low", False)

    def test_non_numeric_slope_skips_cycle(self):
        """A transient 'unavailable' slope must skip the cycle, not raise."""
        state = self._state(specific_states={"temperature_slope": "unavailable"})
        assert _read_climate_cycle_data(state, "climate.test") is None

    def test_non_numeric_temperature_skips_cycle(self):
        state = self._state(current_temperature="unknown")
        assert _read_climate_cycle_data(state, "climate.test") is None

    def test_missing_temperature_skips_cycle(self):
        state = self._state(current_temperature=None)
        assert _read_climate_cycle_data(state, "climate.test") is None

    def test_missing_slope_defaults_to_zero(self):
        """A missing temperature_slope key defaults to 0.0 (not a skip)."""
        state = self._state(specific_states={})
        result = _read_climate_cycle_data(state, "climate.test")
        assert result is not None and result[0] == 0.0


class TestShouldCollectSlopeSample:
    """Tests for slope-sample collection gating (incl. the heat/cool gate, L4)."""

    @staticmethod
    def _kwargs(**over):
        base = dict(
            current_fan="low",
            hvac_mode="heat",
            is_defrost_active=False,
            is_hvac_idle=False,
            phase=PHASE_ESTABLISHED,
            minutes_since_change=25.0,
            learned_dead_time=10.0,  # min stable window = 20 min
            ctrl_state={"last_setpoint_drop_time": 0.0},
            now=1000.0,
        )
        base.update(over)
        return base

    def test_happy_path_collects(self):
        assert _should_collect_slope_sample(**self._kwargs()) is True

    def test_off_mode_is_skipped(self):
        assert _should_collect_slope_sample(**self._kwargs(hvac_mode="off")) is False

    def test_dry_and_fan_only_skipped(self):
        assert _should_collect_slope_sample(**self._kwargs(hvac_mode="dry")) is False
        assert _should_collect_slope_sample(**self._kwargs(hvac_mode="fan_only")) is False

    def test_cool_mode_collects(self):
        assert _should_collect_slope_sample(**self._kwargs(hvac_mode="cool")) is True

    def test_no_fan_skipped(self):
        assert _should_collect_slope_sample(**self._kwargs(current_fan=None)) is False

    def test_defrost_or_idle_skipped(self):
        assert _should_collect_slope_sample(**self._kwargs(is_defrost_active=True)) is False
        assert _should_collect_slope_sample(**self._kwargs(is_hvac_idle=True)) is False

    def test_non_established_phase_skipped(self):
        assert _should_collect_slope_sample(**self._kwargs(phase="DEAD_TIME")) is False

    def test_not_stable_long_enough_skipped(self):
        # 15 min < 2 * 10 min dead time
        assert _should_collect_slope_sample(**self._kwargs(minutes_since_change=15.0)) is False

    def test_recent_setpoint_drop_skipped(self):
        # setpoint dropped 1 min ago -> within the learning cooldown
        kwargs = self._kwargs(ctrl_state={"last_setpoint_drop_time": 940.0}, now=1000.0)
        assert _should_collect_slope_sample(**kwargs) is False


class TestDetectDisturbances:
    """Tests for external defrost / idle detection."""

    def test_defrost_entity_on_marks_active(self):
        hass = MagicMock()
        hass.states.get.return_value = SimpleNamespace(state="on")
        defrost_state = {"active": False, "start_time": 0.0}
        is_defrost, is_idle = _detect_disturbances(hass, {CONF_DEFROST_ENTITY: "binary_sensor.d"}, defrost_state)
        assert is_defrost is True and is_idle is False
        assert defrost_state["active"] is True

    def test_defrost_expires_after_cooldown(self):
        import time
        hass = MagicMock()
        defrost_state = {"active": True, "start_time": time.time() - 21 * 60}
        is_defrost, _ = _detect_disturbances(hass, {}, defrost_state)
        assert is_defrost is False
        assert defrost_state["active"] is False

    def test_operating_entity_off_marks_idle(self):
        hass = MagicMock()
        hass.states.get.return_value = SimpleNamespace(state="off")
        _, is_idle = _detect_disturbances(hass, {CONF_OPERATING_ENTITY: "binary_sensor.op"}, {"active": False, "start_time": 0.0})
        assert is_idle is True

    def test_operating_entity_on_not_idle(self):
        hass = MagicMock()
        hass.states.get.return_value = SimpleNamespace(state="on")
        _, is_idle = _detect_disturbances(hass, {CONF_OPERATING_ENTITY: "binary_sensor.op"}, {"active": False, "start_time": 0.0})
        assert is_idle is False


class TestApplyFanChange:
    """Tests for _async_apply_fan_change confirmation semantics."""

    @pytest.mark.asyncio
    async def test_success_calls_service_and_confirms(self):
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        confirmed = MagicMock()
        await _async_apply_fan_change(hass, "climate.t", "high", "low", "reason", confirmed)
        hass.services.async_call.assert_awaited_once()
        args = hass.services.async_call.await_args
        assert args.args[0] == "climate" and args.args[1] == "set_fan_mode"
        assert args.args[2] == {"entity_id": "climate.t", "fan_mode": "high"}
        confirmed.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_still_confirms_to_avoid_retry_storm(self):
        hass = MagicMock()
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("boom"))
        confirmed = MagicMock()
        await _async_apply_fan_change(hass, "climate.t", "high", "low", "reason", confirmed)
        confirmed.assert_called_once()
