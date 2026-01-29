"""Tests for SmartFanController logic - COOL mode."""
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

class TestSmartFanControllerCool:
    """Exhaustive test suite for COOL mode decision logic."""

    def test_emergency(self, controller):
        """Test Scenario A: Emergency trigger when room is too hot."""
        result = controller.calculate_decision(
            current_temp=21.0, # +1.0 error
            target_temp=20.0,
            vtherm_slope=0.0,
            hvac_mode="cool",
            current_fan="low"
        )
        assert result["fan_mode"] == "turbo"
        assert "Emergency: High error" in result["reason"]

    def test_braking_anticipation(self, controller):
        """Test Scenario B: Reducing speed before over-cooling (overshoot)."""
        # Setup: Temperature is close, but falling very fast
        # We need a slope change > 0.1 compared to the previous state
        controller._previous_slope = -0.3

        result = controller.calculate_decision(
            current_temp=20.1,
            target_temp=20.0,
            vtherm_slope=-1.2, # Significant acceleration
            hvac_mode="cool",
            current_fan="high"
        )
        assert result["fan_mode"] == "medium"
        assert "Braking: Target overshoot predicted" in result["reason"]

    def test_recovery_relance(self, controller):
        """Test Scenario C: Error persists (too warm) and cooling is stagnant."""
        # Setup: Last slope was 0.0, current is 0.0 (no progress)
        controller._previous_slope = 0.0

        result = controller.calculate_decision(
            current_temp=20.4, # Error 0.4 > soft_error
            target_temp=20.0,
            vtherm_slope=0.0,
            hvac_mode="cool",
            current_fan="low"
        )
        assert result["fan_mode"] == "medium"
        assert "Soft recovery: Drop predicted" in result["reason"]

    def test_comfort_drift(self, controller):
        """Test Scenario D: Small error but temperature starts rising (drift)."""
        result = controller.calculate_decision(
            current_temp=20.1, # Tiny error
            target_temp=20.0,
            vtherm_slope=0.3, # But rising
            hvac_mode="cool",
            current_fan="medium"
        )
        assert result["fan_mode"] == "high"
        assert "Maintenance: Slow drift detected" in result["reason"]

    def test_over_target_reduction(self, controller):
        """Test Scenario E: Reducing fan when it's already colder than target."""
        result = controller.calculate_decision(
            current_temp=19.5,
            target_temp=20.0,
            vtherm_slope=-0.5,
            hvac_mode="cool",
            current_fan="medium"
        )
        assert result["fan_mode"] == "low"
        assert "Over-target: Reducing speed" in result["reason"]

    def test_snapshot_stability(self, controller):
        """Test the snapshot mechanism in cool mode."""
        # 1. First change sets the snapshot
        result = controller.calculate_decision(21.0, 20.0, -0.5, "cool", "low")
        first_snapshot = controller._previous_slope
        assert first_snapshot == -0.5

        # 2. Minor change (0.05) should NOT update snapshot
        controller.calculate_decision(20.9, 20.0, -0.55, "cool", result.get("fan_mode"))
        assert controller._previous_slope == first_snapshot

        # 3. Significant change (0.2) SHOULD update snapshot
        controller.calculate_decision(20.8, 20.0, -0.75, "cool", result.get("fan_mode"))
        assert controller._previous_slope == -0.75

    def test_stable_above_target_with_custom_deadband(self):
        """Test issue: stable temperature above target should still cool to reach setpoint.
        
        Similar to heat mode scenario:
        - Setpoint changed from 20.5°C to 20°C
        - Current temp is 20.2°C (error = 0.2°C, within custom deadband of 0.4°C)
        - Slope is near zero (stable)
        - System should increase cooling to reach target, not just maintain
        """
        controller = SmartFanController(
            fan_modes=FAN_MODES,
            deadband=0.4,  # Custom deadband
            min_interval=DEFAULT_MIN_INTERVAL,
            soft_error=DEFAULT_SOFT_ERROR,
            hard_error=DEFAULT_HARD_ERROR,
        )
        
        # Simulate stable temperature above target
        controller._previous_slope = 0.01
        controller._last_change_time = controller._now - (20 * 60)  # 20 minutes ago
        
        result = controller.calculate_decision(
            current_temp=20.2,
            target_temp=20.0,
            vtherm_slope=0.0,  # Stable (not drifting)
            hvac_mode="cool",
            current_fan="low"
        )
        
        # System should increase fan to reach target
        assert result["fan_mode"] == "medium", f"Expected fan increase, got {result['fan_mode']} with reason: {result['reason']}"
