"""Tests for SmartFanController logic - HEAT mode."""
import pytest

from custom_components.smart_fan_controller.controller import SmartFanController
from custom_components.smart_fan_controller.const import (
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SOFT_ERROR,
    DEFAULT_HARD_ERROR,
)

# Standard configuration for tests
FAN_MODES = ["low", "medium", "high", "turbo"]
DEFAULT_CONFIG = {
    "deadband": DEFAULT_DEADBAND,
    "min_interval": DEFAULT_MIN_INTERVAL,
    "soft_error": DEFAULT_SOFT_ERROR,
    "hard_error": DEFAULT_HARD_ERROR,
}

@pytest.fixture
def controller():
    """Fixture to provide a clean controller instance."""
    return SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)

class TestSmartFanControllerHeat:
    """Exhaustive test suite for HEAT decision logic."""

    def test_emergency(self, controller):
        """Test Scenario A: Emergency trigger when error is very high."""
        result = controller.calculate_decision(
            current_temp=19.0, # -1.0 error
            target_temp=20.0,
            vtherm_slope=0.0,
            hvac_mode="heat",
            current_fan="low"
        )
        assert result["fan_mode"] == "turbo"
        assert "Emergency: High error" in result["reason"]

    def test_braking_anticipation(self, controller):
        """Test Scenario B: Reducing speed before over-heating (overshoot)."""
        # Setup: Temperature is close, but rising very fast
        # We need a slope change > 0.1 compared to the previous state
        controller._previous_slope = 0.3

        result = controller.calculate_decision(
            current_temp=19.9,
            target_temp=20.0,
            vtherm_slope=1.2, # Significant acceleration
            hvac_mode="heat",
            current_fan="high"
        )
        assert result["fan_mode"] == "medium"
        assert "Braking: Target overshoot predicted" in result["reason"]

    def test_recovery_relance(self, controller):
        """Test Scenario C: Error persists and slope is not improving."""
        # Setup: Last slope was 0.0, current is 0.0 (no progress)
        controller._previous_slope = 0.0

        result = controller.calculate_decision(
            current_temp=19.6, # Error 0.4 > soft_error
            target_temp=20.0,
            vtherm_slope=0.0,
            hvac_mode="heat",
            current_fan="low"
        )
        assert result["fan_mode"] == "medium"
        assert "Soft recovery: Drop predicted" in result["reason"]

    def test_comfort_drift(self, controller):
        """Test Scenario D: Small error but temperature starts drifting away."""
        result = controller.calculate_decision(
            current_temp=19.9, # Tiny error
            target_temp=20.0,
            vtherm_slope=-0.3, # But dropping
            hvac_mode="heat",
            current_fan="medium"
        )
        assert result["fan_mode"] == "high"
        assert "Maintenance: Slow drift detected" in result["reason"]

    def test_over_target_reduction(self, controller):
        """Test Scenario E: Reducing fan when target is exceeded."""
        result = controller.calculate_decision(
            current_temp=20.5,
            target_temp=20.0,
            vtherm_slope=-0.5,
            hvac_mode="heat",
            current_fan="medium"
        )
        assert result["fan_mode"] == "low"
        assert "Over-target: Reducing speed" in result["reason"]

    def test_snapshot_stability(self, controller):
        """Test the snapshot mechanism: stability with minor slope noise."""
        # 1. First change sets the snapshot
        result = controller.calculate_decision(19.0, 20.0, 0.5, "heat", "low")
        first_snapshot = controller._previous_slope
        assert first_snapshot == 0.5

        # 2. Minor change (0.05) should NOT update snapshot
        controller.calculate_decision(19.1, 20.0, 0.55, "heat", result.get("fan_mode"))
        assert controller._previous_slope == first_snapshot

        # 3. Significant change (0.2) SHOULD update snapshot
        controller.calculate_decision(19.2, 20.0, 0.75, "heat", result.get("fan_mode"))
        assert controller._previous_slope == 0.75

    def test_stable_below_target_with_custom_deadband(self):
        """Test issue: stable temperature below target should still heat to reach setpoint.
        
        Reproduces user scenario:
        - Setpoint changed from 17.5°C to 18°C
        - Current temp is 17.8°C (error = 0.2°C, within custom deadband of 0.4°C)
        - Slope is near zero (stable)
        - System should increase heating to reach target, not just maintain
        """
        controller = SmartFanController(
            fan_modes=FAN_MODES,
            deadband=0.4,  # User's custom deadband
            min_interval=DEFAULT_MIN_INTERVAL,
            soft_error=DEFAULT_SOFT_ERROR,
            hard_error=DEFAULT_HARD_ERROR,
        )
        
        # Simulate stable temperature below target
        controller._previous_slope = -0.01
        controller._last_change_time = controller._now - (20 * 60)  # 20 minutes ago
        
        result = controller.calculate_decision(
            current_temp=17.8,
            target_temp=18.0,
            vtherm_slope=-0.0,  # Stable (not drifting)
            hvac_mode="heat",
            current_fan="low"
        )
        
        # System should increase fan to reach target
        assert result["fan_mode"] == "medium", f"Expected fan increase, got {result['fan_mode']} with reason: {result['reason']}"
