"""Test for learning enabled switch functionality."""
import pytest
import time
from custom_components.smart_fan_controller.controller import SmartFanController


class TestLearningEnabledFeature:
    """Test the learning_enabled flag functionality."""

    def test_learning_enabled_default_true(self):
        """Test that learning is enabled by default."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
        )
        assert controller.learning_enabled is True

    def test_learning_can_be_disabled(self):
        """Test that learning can be disabled during initialization."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
            learning_enabled=False,
        )
        assert controller.learning_enabled is False

    def test_learning_enabled_prevents_slope_collection(self):
        """Test that disabling learning prevents slope sample collection."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
            learning_enabled=False,
        )

        # Make a decision that would normally collect learning data
        decision = controller.calculate_decision(
            current_temp=22.0,
            target_temp=21.0,
            vtherm_slope=0.5,
            hvac_mode="heat",
            current_fan="medium"
        )

        # Verify no samples were collected
        assert controller.learning.slope_sample_count() == 0

    def test_learning_enabled_allows_slope_collection(self):
        """Test that enabling learning allows slope sample collection."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
            learning_enabled=True,
        )

        # Ensure the fan mode has been active long enough for the stable-duration filter
        controller._last_change_time = time.time() - 1800  # 30 min ago

        # Make a decision that would collect learning data
        decision = controller.calculate_decision(
            current_temp=22.0,
            target_temp=21.0,
            vtherm_slope=0.5,
            hvac_mode="heat",
            current_fan="medium"
        )

        # Verify samples were collected (temperature_error = 1.0, which is > 0)
        assert controller.learning.slope_sample_count() > 0

    def test_learning_can_be_toggled_at_runtime(self):
        """Test that learning can be toggled on/off at runtime."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
            learning_enabled=True,
        )

        # Ensure the fan mode has been active long enough for the stable-duration filter
        controller._last_change_time = time.time() - 1800  # 30 min ago

        # Collect some samples with learning enabled (positive error so it's not skipped)
        controller.calculate_decision(
            current_temp=20.0,
            target_temp=21.0,
            vtherm_slope=0.5,
            hvac_mode="heat",
            current_fan="medium"
        )
        initial_count = controller.learning.slope_sample_count()
        assert initial_count > 0

        # Disable learning and verify no new samples are collected
        controller.learning_enabled = False
        controller.calculate_decision(
            current_temp=20.5,
            target_temp=21.0,
            vtherm_slope=0.4,
            hvac_mode="heat",
            current_fan="medium"
        )
        assert controller.learning.slope_sample_count() == initial_count

        # Re-enable learning and verify samples are collected again
        controller.learning_enabled = True
        controller.calculate_decision(
            current_temp=20.2,
            target_temp=21.0,
            vtherm_slope=0.3,
            hvac_mode="heat",
            current_fan="medium"
        )
        assert controller.learning.slope_sample_count() > initial_count
