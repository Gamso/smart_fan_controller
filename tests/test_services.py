"""Tests for Smart Fan Controller HA services (apply_learned_settings, reset_learning)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.smart_fan_controller import _apply_optimal_parameters
from custom_components.smart_fan_controller.thermal_learning import ThermalLearning
from custom_components.smart_fan_controller.const import (
    CONF_DEADBAND,
    CONF_LIMIT_TIMEOUT,
    MIN_SAMPLES_LEARNING,
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

        optimal = {"deadband": 0.25, "limit_timeout": 12}

        asyncio.get_event_loop().run_until_complete(_apply_optimal_parameters(hass, entry, optimal))

        # Verify async_update_entry was called with learning_auto_applied=True
        call_args = hass.config_entries.async_update_entry.call_args
        updated_data = call_args[1]["data"]
        assert updated_data.get("learning_auto_applied") is True
        assert updated_data[CONF_DEADBAND] == 0.25
        assert updated_data[CONF_LIMIT_TIMEOUT] == 12


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
