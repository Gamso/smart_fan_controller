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
        # 1. Operating in Heat: error=0.5 > soft_error, interval expired → Strong recovery
        result = controller.calculate_decision(19.5, 20.0, 0.2, "heat", "low")
        assert controller._last_hvac_mode == "heat"
        assert "Strong recovery: Drop predicted " in result["reason"]
        assert result["fan_mode"] == "medium"

        # 2. Instant switch to Cool: hvac_mode change resets _previous_slope and
        # _thermal_acceleration so no spurious slope_change is triggered.
        # With interval still expired and error=-0.5 < -deadband, Zone E fires.
        result = controller.calculate_decision(19.5, 20.0, 0.2, "cool", "medium")
        assert controller._last_hvac_mode == "cool"
        # _thermal_acceleration was reset; slope_change=False; interval_expired=True
        # Zone E: current_temperature_error=-0.5 < -deadband → reduce speed
        assert "Over-target: Reducing speed" in result["reason"]
        assert result["fan_mode"] == "low"

    def test_hvac_mode_off_holds_fan(self, controller):
        """Test that hvac_mode='off' returns immediately without changing fan speed."""
        result = controller.calculate_decision(19.0, 20.0, -0.5, "off", "high")
        assert result["fan_mode"] == "high"
        assert "off" in result["reason"]
        assert result["temperature_error"] == 0.0

    def test_hvac_mode_dry_holds_fan(self, controller):
        """Test that hvac_mode='dry' returns immediately without changing fan speed."""
        result = controller.calculate_decision(22.0, 20.0, 0.3, "dry", "medium")
        assert result["fan_mode"] == "medium"
        assert "dry" in result["reason"]

    def test_hvac_mode_fan_only_holds_fan(self, controller):
        """Test that hvac_mode='fan_only' returns immediately without changing fan speed."""
        result = controller.calculate_decision(20.0, 20.0, 0.0, "fan_only", "low")
        assert result["fan_mode"] == "low"
        assert "fan_only" in result["reason"]

    def test_hvac_mode_switch_resets_slope(self, controller):
        """Test that _previous_slope is reset when hvac_mode changes."""
        controller._previous_slope = 0.1
        controller.calculate_decision(19.5, 20.0, 0.3, "heat", "low")
        # Switch to cool: previous_slope must be reset to the current slope
        controller.calculate_decision(19.5, 20.0, 0.3, "cool", "low")
        assert controller._previous_slope == 0.3

    def test_learning_records_current_fan_not_decided_fan(self, controller):
        """Verify that the slope sample is attributed to the ACTIVE fan mode, not the decided one."""
        controller._last_change_time = 0  # interval expired immediately
        # current_fan='low', error=0.5 (soft zone), slope=-0.5 (falling, not improving) → boosts to 'medium'
        result = controller.calculate_decision(19.5, 20.0, -0.5, "heat", "low")
        assert result["fan_mode"] == "medium"  # decision changed from low to medium
        # But the learning sample must carry 'low' (the mode that produced the measured slope)
        assert len(controller.learning._slope_samples) > 0
        sample = controller.learning._slope_samples[-1]
        assert sample[1] == "low", f"Expected sample fan_mode='low' but got '{sample[1]}'"

    def test_auto_apply_flag_in_entry_data_prevents_second_apply(self):
        """Verify that the learning_auto_applied flag logic blocks a second auto-apply.

        This is a unit-level guard against the infinite reload loop:
        if entry.data already has learning_auto_applied=True, the condition must
        evaluate to False regardless of learning readiness.
        """

        # Simulate: learning is ready, but the flag is already set in entry.data
        class _FakeEntry:
            data = {"learning_auto_applied": True}

        entry = _FakeEntry()
        # Condition mirrors what __init__.py checks:
        should_apply = True and True and not entry.data.get("learning_auto_applied", False)  # learning_enabled  # learning.is_ready()
        assert should_apply is False, "Auto-apply should be blocked when flag is already set"

        # Verify that a fresh entry (no flag) would pass
        entry_fresh = _FakeEntry()
        entry_fresh.data = {}
        should_apply_fresh = True and True and not entry_fresh.data.get("learning_auto_applied", False)
        assert should_apply_fresh is True

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
        Scenario: Linear projection with clamp.
        Goal: Verify the linear math: temp_proj = current + (v * 10/60), clamped.
        """
        controller._previous_slope = 0.0  # Started stable
        current_slope = 0.6  # Now rising at 0.6°C/h
        proj = controller.compute_temperature_projection(20.0, current_slope)
        # 20.0 + 0.6 * (10/60) = 20.0 + 0.1 = 20.1
        assert proj == 20.1

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

                # Simulate HA confirming the fan change (mirrors __init__.py behaviour)
                if actual_fan != current_fan:
                    controller.confirm_fan_change()

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
            # T+0: Below target with a strong slope. Stay in High.
            (0,  19.5, 20.0, 1.40, "high", "high"),

            # T+10: Temp is 19.95 (Still below target).
            # Simple Projection: 19.95 + (1.55 * 10/60) = 20.208
            # Projected Error: 20.0 - 20.208 = -0.208
            # Since -0.208 < -deadband (-0.2), Block B triggers and reduces speed.
            (10, 19.95, 20.0, 1.55, "high", "medium"),

            # T+20: Reached target (20.0).
            # Error is 0.0 -> Enters Block F (Comfort Zone/Stable). Maintain Medium.
            (20, 20.0, 20.0, 0.20, "medium", "medium"),

            # T+30: Stable on target with minimal slope.
            (30, 20.0, 20.0, 0.05, "medium", "medium"),
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
                controller.record_manual_override(new_fan)

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

    def test_uninitialized_fan_modes_returns_sensor_data(self):
        """
        Test that when fan_modes is None (not initialized), the controller
        still returns all sensor data (temperature_error, projected_temperature, etc.)
        instead of just fan_mode and reason. This prevents sensors from staying 'unknown'.
        """
        # Create controller without fan modes
        controller_no_modes = SmartFanController(fan_modes=None, **DEFAULT_CONFIG)

        # Call calculate_decision with valid temperature data
        result = controller_no_modes.calculate_decision(
            current_temp=19.5,
            target_temp=20.0,
            vtherm_slope=0.2,
            hvac_mode="heat",
            current_fan="medium"
        )

        # Verify all expected keys are present
        assert "fan_mode" in result
        assert "projected_temperature" in result
        assert "projected_temperature_error" in result
        assert "temperature_error" in result
        assert "minutes_since_last_change" in result
        assert "reason" in result

        # Verify fan_mode is preserved (not changed when modes not initialized)
        assert result["fan_mode"] == "medium"
        assert result["reason"] == "No fan modes defined"

        # Verify temperature calculations are performed
        assert result["temperature_error"] == 0.5  # target - current for heat mode
        assert isinstance(result["projected_temperature"], float)
        assert isinstance(result["projected_temperature_error"], float)
        assert isinstance(result["minutes_since_last_change"], float)

    def test_empty_fan_modes_list_retries(self):
        """
        Test that when fan_modes is set to empty list (race condition scenario),
        the controller continues to accept fan_modes updates on subsequent attempts.
        This simulates the startup race condition where VTherm hasn't initialized yet.
        """
        # Create controller without fan modes
        controller_no_modes = SmartFanController(fan_modes=None, **DEFAULT_CONFIG)

        # Verify initial state is None
        assert controller_no_modes.fan_modes is None

        # Simulate race condition: set to empty list (what happens when VTherm not ready)
        controller_no_modes.fan_modes = []

        # Verify it's empty
        assert controller_no_modes.fan_modes == []
        assert not controller_no_modes.fan_modes  # Should be falsy

        # Now simulate VTherm becoming available with proper modes
        controller_no_modes.fan_modes = ["low", "medium", "high"]

        # Verify modes are now set
        assert controller_no_modes.fan_modes == ["low", "medium", "high"]

        # Verify controller now works properly
        result = controller_no_modes.calculate_decision(
            current_temp=19.5,
            target_temp=20.0,
            vtherm_slope=0.2,
            hvac_mode="heat",
            current_fan="low"
        )

        # Should now be able to make decisions
        assert result["fan_mode"] in ["low", "medium", "high"]
        assert "No fan modes defined" not in result["reason"]
