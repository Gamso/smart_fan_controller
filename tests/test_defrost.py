"""Tests for defrost protection in the controller."""
# pylint: disable=redefined-outer-name,protected-access
from unittest.mock import patch

import pytest

from custom_components.smart_fan_controller.controller import SmartFanController
from custom_components.smart_fan_controller.const import (
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SOFT_ERROR,
    DEFAULT_HARD_ERROR,
)

FAN_MODES = ["silent", "low", "med", "high", "superhigh"]
DEFAULT_CONFIG = {
    "deadband": DEFAULT_DEADBAND,
    "min_interval": DEFAULT_MIN_INTERVAL,
    "soft_error": DEFAULT_SOFT_ERROR,
    "hard_error": DEFAULT_HARD_ERROR,
}


@pytest.fixture
def controller():
    """Create a SmartFanController instance for testing."""
    return SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)


class TestDefrostProtection:
    """Tests that defrost protection blocks step-down decisions."""

    def test_defrost_blocks_braking(self, controller):
        """During defrost, braking (step-down on overshoot) should be blocked when under target."""
        # Set up controller state: strong positive slope that triggers B (braking)
        # but we're still under target, so defrost should protect
        controller._previous_slope = 0.0
        controller._now = 0.0
        controller._last_change_time = 0.0
        controller._defrost_active = True
        controller._defrost_start_time = 0.0
        controller._last_change_time = 0.0

        with patch("time.time", return_value=1200.0):  # 20 min later
            result = controller.calculate_decision(
                current_temp=19.9,
                target_temp=20.0,
                vtherm_slope=2.0,  # Very strong slope that would cause braking
                hvac_mode="heat",
                current_fan="high",
            )

        assert "Defrost hold" in result["reason"]
        assert result["fan_mode"] == "high"  # Should NOT step down

    def test_defrost_blocks_favorable_slope_reduction(self, controller):
        """During defrost, maintenance favorable slope reduction should be blocked."""
        controller._previous_slope = 0.0
        controller._slope_at_last_change = 0.0
        controller._now = 0.0
        controller._last_change_time = 0.0
        controller._defrost_active = True
        controller._defrost_start_time = 0.0
        controller._last_change_time = 0.0

        with patch("time.time", return_value=1200.0):
            result = controller.calculate_decision(
                current_temp=19.85,
                target_temp=20.0,
                vtherm_slope=0.6,
                hvac_mode="heat",
                current_fan="superhigh",
            )

        assert "Defrost hold" in result["reason"]
        assert result["fan_mode"] == "superhigh"

    def test_defrost_allows_emergency(self, controller):
        """Emergency should still work during defrost."""
        controller._defrost_active = True
        controller._defrost_start_time = 1000.0

        with patch("time.time", return_value=1200.0):
            result = controller.calculate_decision(
                current_temp=18.0,
                target_temp=20.0,
                vtherm_slope=0.0,
                hvac_mode="heat",
                current_fan="low",
            )

        assert "Emergency" in result["reason"]
        assert result["fan_mode"] == "superhigh"

    def test_defrost_output_includes_flag(self, controller):
        """The decision output should include defrost_active flag."""
        with patch("time.time", return_value=1000.0):
            result = controller.calculate_decision(
                current_temp=20.0,
                target_temp=20.0,
                vtherm_slope=0.0,
                hvac_mode="heat",
                current_fan="med",
            )

        assert "defrost_active" in result
        assert result["defrost_active"] is False


class TestDefrostLearningProtection:
    """Tests that learning samples are excluded during defrost."""

    def test_learning_skipped_during_defrost(self, controller):
        """Learning should NOT collect slope samples during active defrost."""
        controller._defrost_active = True
        controller._defrost_start_time = 1000.0
        initial_count = controller.learning.slope_sample_count()

        with patch("time.time", return_value=1200.0):
            controller.calculate_decision(
                current_temp=19.5,
                target_temp=20.0,
                vtherm_slope=0.8,
                hvac_mode="heat",
                current_fan="high",
            )

        assert controller.learning.slope_sample_count() == initial_count

    def test_response_events_excluded_during_defrost(self, controller):
        """Response time events should NOT be recorded during active defrost."""
        controller._defrost_active = True
        controller._defrost_start_time = 1000.0
        controller.previous_slope = 0.0
        controller._last_change_time = 1000.0 - (5 * 60)  # 5 min ago
        initial_events = controller.learning.response_event_count()

        # slope change from 0.0 → 0.8 would normally trigger a response event
        with patch("time.time", return_value=1000.0):
            controller.calculate_decision(
                current_temp=19.5,
                target_temp=20.0,
                vtherm_slope=0.8,
                hvac_mode="heat",
                current_fan="high",
            )

        assert controller.learning.response_event_count() == initial_events

    def test_learning_resumes_after_defrost_expires(self, controller):
        """Learning should resume after defrost cooldown expires."""
        controller._defrost_active = True
        controller._defrost_start_time = 0.0
        controller._last_change_time = 0.0
        initial_count = controller.learning.slope_sample_count()

        # 25 minutes after defrost start → cooldown expired
        with patch("time.time", return_value=1500.0):
            controller.calculate_decision(
                current_temp=19.5,
                target_temp=20.0,
                vtherm_slope=0.8,
                hvac_mode="heat",
                current_fan="high",
            )

        # Learning should have collected a sample (slope 0.8 > 0.15 threshold)
        assert controller.learning.slope_sample_count() > initial_count
