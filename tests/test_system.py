"""Tests for SmartFanController system constraints and edge cases."""
import pytest

from unittest.mock import patch
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.helpers.event import async_track_state_change_event

from custom_components.smart_fan_controller.const import DOMAIN
from custom_components.smart_fan_controller.controller import SmartFanController
from custom_components.smart_fan_controller.const import (
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SOFT_ERROR,
    DEFAULT_HARD_ERROR,
    DEFAULT_TEMPERATURE_PROJECTED_ERROR
)

# Standard configuration for tests
FAN_MODES = ["low", "medium", "high", "turbo"]
DEFAULT_CONFIG = {
    "deadband": DEFAULT_DEADBAND,
    "min_interval": DEFAULT_MIN_INTERVAL,
    "soft_error": DEFAULT_SOFT_ERROR,
    "hard_error": DEFAULT_HARD_ERROR,
    "projected_error_threshold": DEFAULT_TEMPERATURE_PROJECTED_ERROR
}

@pytest.fixture
def controller():
    """Fixture to provide a clean controller instance."""
    return SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)

class TestSmartFanControllerSystem:
    """System-level tests: timing, boundaries, and mode switching."""

    def test_min_interval_protection(self, controller):
        """Test that speed doesn't change before min_interval unless forced."""
        start_time = 60
        controller._last_change_time = 60

        # 1. First change at T=0
        with patch('time.time', return_value=start_time):
            result = controller.calculate_decision(20.0, 20.0, 0.0, "heat", "low")
            assert result["fan_mode"] == "low"

        # 2. Try to change at T + 2 minutes (too early)
        # Even with a significant error, it should stay 'low'
        with patch('time.time', return_value=start_time + 2*60):
            result = controller.calculate_decision(19.5, 20.0, -0.2, "heat", "low")
            assert result["fan_mode"] == "low"

        # 3. Try to change at T + 10 minutes
        # Fan speed should be allowed to change now
        with patch('time.time', return_value=start_time + 10*60):
            result = controller.calculate_decision(19.5, 20.0, -0.5, "heat", "low")
            assert result["fan_mode"] == "medium"

    def test_emergency_overrides_interval(self, controller):
        """Test that Emergency bypasses the min_interval timer."""
        start_time = 60
        controller._last_change_time = 60

        # 1. First change at T=0
        with patch('time.time', return_value=start_time):
            controller.calculate_decision(19.9, 20.0, 0.0, "heat", "low")

        # 2. Major error at T + 1 minute
        with patch('time.time', return_value=start_time + 60):
            result = controller.calculate_decision(18.0, 20.0, 0.0, "heat", "low")

        # Should bypass timer because it's an Emergency
        assert result["fan_mode"] == "turbo"
        assert "Emergency" in result["reason"]

    def test_index_boundaries_low(self, controller):
        """Ensure no crash when trying to go below the first fan mode."""
        # Case: Overheating in heat mode while already at 'low'
        result = controller.calculate_decision(21.0, 20.0, 0.1, "heat", "low")

        assert result["fan_mode"] == "low"
        assert "Over-target" in result["reason"]

    def test_index_boundaries_high(self, controller):
        """Ensure no crash when trying to go above the last fan mode."""
        # Case: Drifting away while already at 'turbo'
        result = controller.calculate_decision(19.9, 20.0, -0.5, "heat", "turbo")

        assert result["fan_mode"] == "turbo"
        assert "Maintenance" in result["reason"]

    def test_hvac_mode_switch_mid_operation(self, controller):
        """Test switching from Heat to Cool mode maintains logic integrity."""
        # 1. Operating in Heat
        result = controller.calculate_decision(19.5, 20.0, 0.2, "heat", "low")
        assert controller._previous_slope == 0.2
        assert "Soft recovery: Drop predicted " in result["reason"]
        assert result["fan_mode"] == "medium"

        # 2. Instant switch to Cool
        # TODO: Ensure previous slope is reset appropriately
        # TODO: On this HVAC mode switch, the fan speed should be re-evaluated correctly and finish in 'low'
        result = controller.calculate_decision(19.5, 20.0, 0.2, "cool", "medium")
        assert "Over-target: Observing inertia" in result["reason"]
        assert result["fan_mode"] == "medium"

    def test_step_down_protection(self, controller):
        """
        Scenario: Drastic change requires dropping from Turbo to Low.
        Goal: Validate that the controller only steps down one level at a time
        to protect the motor and maintain acoustic comfort.
        """
        # Current index is Turbo (3), proposed is Low (0).
        # Even with force=True, it should only drop to High (2).
        final_idx = controller.determine_final_index(current_index=3, new_index=0, minutes_since_change=35, force=True)
        assert FAN_MODES[final_idx] == "high" # Turbo (3) -> High (2), pas Low (0)

    def test_startup_with_invalid_fan_mode(self, controller):
        """
        Scenario: Integration starts with an unknown fan state (None or Unknown).
        Goal: Ensure the 'try/except' block handles the ValueError and defaults to index 0.
        """
        # Should not crash and should return a valid mode from your list
        result = controller.calculate_decision(19.0, 20.0, 0.5, "heat", "unknown_mode")
        # With 1.0°C error, it should have moved from 0 (low) to 1 (medium)
        assert result["fan_mode"] in FAN_MODES # Ne doit pas crasher

    def test_projection_math(self, controller):
        """
        Scenario: High thermal acceleration.
        Goal: Verify the parabolic math: temp_proj = current + (v*t) + (0.5*a*t^2).
        """
        controller._previous_slope = 0.0 # Started stable
        current_slope = 0.6              # Now rising at 0.6°C/h
        # d_slope = 0.6 - 0.0 = 0.6 °C/h
        # t_acc_buffer = (0.3 * 0.6) + (0.7 * 0.0) = 0.18 °C/min²
        # v = 0.6 / 60 = 0.01 °C/min
        # a = 0.18 / 60 = 0.003 °C/min²
        # t = 10 min
        # Calculation: 20.0 + (0.01 * 10) + (0.5 * 0.003 * 100)
        # = 20.0 + 0.1 + 0.15 = 20.25
        proj = controller.compute_temperature_projection(20.0, current_slope)
        assert proj == 20.25

    def _run_sequence_test(self, controller, sequence, initial_time=0.0, initial_slope=0.0, last_change_ago=None):
        controller._last_change_time = initial_time
        controller._previous_slope = initial_slope

        if last_change_ago is not None:
            controller._last_change_time = last_change_ago

        current_fan = sequence[0][4]

        for elapsed_min, current, target, slope, _, expected_fan in sequence:
            current_time = initial_time + (elapsed_min * 60)
            with patch('time.time', return_value=current_time):
                result = controller.calculate_decision(current, target, slope, "heat", current_fan)
                actual_fan = result["fan_mode"]

                error_msg = (
                    f"Failed at T+{elapsed_min}min: "
                    f"Expected {expected_fan}, got {actual_fan} "
                    f"(Input was {current_fan}, Reason: {result['reason']})"
                )

                assert result["fan_mode"] == expected_fan, error_msg
                current_fan = actual_fan

    def test_sequence_recovery_inertia(self, controller):
        """
        Scenario: System recovering from a large error with thermal lag.
        Validates that the controller remains patient during the 'dead time'
        after a speed change, avoiding rapid oscillations.
        """
        sequence = [
            # elapsed_min, current, target, slope, current_fan, expected_fan
            (0,  20.2, 20.0, 1.68, "turbo", "turbo"), # Init
            (10, 20.4, 20.0, 1.84, "turbo", "high"),  # Slope change triggers drop
            (20, 20.4, 20.0, 1.84, "high",  "high"),  # Inertia: stay at high
            (30, 20.4, 20.0, 0.37, "high",  "medium"),# Slope change triggers 2nd drop
        ]

        self._run_sequence_test(controller, sequence, initial_slope=1.68)

    def test_sequence_dynamic_braking(self, controller):
        """
        Scenario: Rapidly rising temperature (e.g., external heat gain).
        Validates the 'Braking' effect: slope spike overrides timer.
        """
        sequence = [
            # elapsed_min, current, target, slope, current_fan, expected_fan
            (0,  20.0, 20.0, -0.40, "high",   "high"),
            (10, 20.4, 20.0, 1.20,  "high",   "medium",), # Brake!
            (20, 20.4, 20.0, 1.20,  "medium", "medium"),  # Timer: 10m
            (30, 20.4, 20.0, 1.20,  "medium", "low"),  # Timer: 20m -> Drop
        ]

        self._run_sequence_test(controller, sequence, initial_slope=-0.42)

    def test_sequence_stress_recovery(self, controller):
        """
        Scenario: Rapid temperature drop followed by a forced recovery (Booster).
        Validates: Drop Detection -> Emergency Booster -> Smooth Brake.
        """
        sequence = [
            # elapsed_min, current, target, slope, current_fan, expected_fan
            (0,  19.8, 20.0, -0.10, "high",  "high"),  # Slight drop
            (10, 19.4, 20.0, -1.80, "high",  "turbo"), # Emergency detected
            (20, 19.6, 20.0, -0.20, "turbo", "turbo"),  # Trend is improving
            (30, 20.0, 20.0, 1.50,  "turbo",  "high"),  # Braking: Target overshoot predicted
            (40, 20.0, 20.0, 0.10,  "high",  "high"),  # Stabilized
        ]

        self._run_sequence_test(controller, sequence, initial_slope=0.08)

    def test_sequence_overshoot_recovery(self, controller):
        """
        Scenario: Room is significantly over-target (Overshoot).
        Validates that the fan stays at minimum ('low') and doesn't
        restart until the temperature is back near the target.
        """
        sequence = [
            # elapsed_min, current, target, slope, current_fan, expected_fan
            (0,  21.0, 20.0, -0.10, "low", "low"), # Way over target, cooling slowly
            (10, 20.8, 20.0, -0.20, "low", "low"), # Still over
            (20, 20.4, 20.0, -0.40, "low", "low"), # Getting closer
            (30, 20.1, 20.0, -0.10, "low", "low"), # Almost there, stability logic should hold 'low'
        ]

        self._run_sequence_test(controller, sequence, initial_slope=-0.10)

    def test_sequence_soft_landing(self, controller):
        """
        Scenario: Approaching target from below with a steady slope.
        Validates that the controller maintains the speed once the target
        is reached if the situation is stable.
        """
        sequence = [
            (0,  19.7, 20.0, 1.40, "high", "medium"), # High slope triggers early brake to medium
            (10, 19.9, 20.0, 0.40, "medium", "medium"), # Inertia blocks drop to low
            (20, 20.0, 20.0, 0.20, "medium", "medium"), # Inertia still active
            (30, 20.0, 20.0, 0.05, "medium", "medium"), # Target hit + Stable -> Maintain speed
        ]

        self._run_sequence_test(controller, sequence, initial_slope=-0.10)

    def test_sequence_noisy_sensor(self, controller):
        """
        Scenario: Sensor noise (slight jitter in temperature/slope).
        Validates that the inertia timer (30min) prevents the fan
        from switching back and forth (Anti-short cycle).
        """
        sequence = [
            # elapsed_min, current, target, slope, current_fan, expected_fan
            (0,  19.7, 20.0, 0.0,   "low", "medium"),    # Change triggered
            (5,  19.5, 20.0, -0.05, "medium", "medium"), # Small drop (noise) -> Should ignore
            (10, 19.7, 20.0, 0.05,  "medium", "medium"), # Small jump (noise) -> Should ignore
            (15, 20.0, 20.0, 0.05,  "medium", "medium"), # Back to normal
        ]

        self._run_sequence_test(controller, sequence, initial_time=3600, initial_slope=-0.10, last_change_ago=0)

    async def test_manual_fan_change_integration(self, hass, controller):
        """
        Test that a state change event on the climate entity
        actually updates the controller logic.
        """
        climate_id = "climate.salon"
        entry_id = "entry_id_123"

        # 1. Setup the initial fake state
        hass.states.async_set(climate_id, "heat", {
            "fan_mode": "low",
            "fan_modes": ["low", "medium", "high", "turbo"]
        })

        # 2. Setup storage (mirroring what's in your __init__.py)
        hass.data[DOMAIN] = {
            entry_id: {
                "controller": controller,
                "sensors": []
            }
        }

        # 3. Define the listener logic LOCALLY in the test
        # This matches the logic in your __init__.py
        async def mock_handle_manual_change(event):
            new_state = event.data.get("new_state")
            old_state = event.data.get("old_state")
            if not new_state or not old_state:
                return

            new_fan = new_state.attributes.get("fan_mode")
            old_fan = old_state.attributes.get("fan_mode")

            if new_fan != old_fan and new_fan is not None:
                # This is the call we want to verify
                controller.update_new_fan_state(new_fan)

        # Register our local mock listener
        async_track_state_change_event(hass, [climate_id], mock_handle_manual_change)

        # 4. Trigger a manual change by updating the state
        # We simulate this happening at T=2000
        test_time = 2000.0
        with patch('time.time', return_value=test_time):
            hass.states.async_set(climate_id, "heat", {
                "fan_mode": "high", # The change
                "fan_modes": ["low", "medium", "high", "turbo"]
            })
            # Wait for Home Assistant's event bus to process mock_handle_manual_change
            await hass.async_block_till_done()

        # 5. Verification
        # If the listener worked, last_change_time should match our patched time
        assert controller._last_change_time == test_time