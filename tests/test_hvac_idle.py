"""Tests for HVAC idle (compressor off) detection and protection."""
# pylint: disable=redefined-outer-name,protected-access
from unittest.mock import patch

import pytest

from custom_components.smart_fan_controller.controller import SmartFanController
from custom_components.smart_fan_controller.mpc_controller import MPCController
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


def _prime_learning_profiles(ctrl: SmartFanController) -> None:
    """Feed enough slope samples for all profiles to become ready."""
    for _ in range(60):
        ctrl.learning.add_slope_sample("silent", 0.15, 0.8, "heat")
        ctrl.learning.add_slope_sample("low", 0.25, 0.8, "heat")
        ctrl.learning.add_slope_sample("med", 0.9, 0.8, "heat")
        ctrl.learning.add_slope_sample("high", 1.5, 0.8, "heat")
        ctrl.learning.add_slope_sample("superhigh", 2.0, 0.8, "heat")
    ctrl.learning.add_response_event(8.0)
    ctrl.learning.add_response_event(10.0)
    ctrl.learning.add_response_event(12.0)


class TestHvacIdleControllerHeat:
    """Controller blocks step-up decisions when HVAC compressor is idle."""

    def test_zone_c_blocked_during_idle(self, controller):
        """Zone C (recovery): step-up should be blocked when compressor is off."""
        with patch("time.time", return_value=3600.0):
            result = controller.calculate_decision(
                current_temp=19.6,
                target_temp=20.0,
                vtherm_slope=0.0,
                hvac_mode="heat",
                current_fan="low",
                is_hvac_idle=True,
            )
        assert result["fan_mode"] == "low"
        assert "HVAC idle" in result["reason"]
        assert result["hvac_idle"] is True

    def test_zone_c_allowed_when_not_idle(self, controller):
        """Zone C (recovery): step-up should work normally when compressor is running."""
        controller._last_change_time = 3600.0 - (DEFAULT_MIN_INTERVAL * 60 + 60)
        with patch("time.time", return_value=3600.0):
            result = controller.calculate_decision(
                current_temp=19.6,
                target_temp=20.0,
                vtherm_slope=0.0,
                hvac_mode="heat",
                current_fan="low",
                is_hvac_idle=False,
            )
        assert result["fan_mode"] == "med"
        assert "recovery" in result["reason"]

    def test_zone_d_blocked_during_idle(self, controller):
        """Zone D (drift): step-up should be blocked when compressor is off."""
        with patch("time.time", return_value=3600.0):
            result = controller.calculate_decision(
                current_temp=19.9,
                target_temp=20.0,
                vtherm_slope=-0.3,
                hvac_mode="heat",
                current_fan="med",
                is_hvac_idle=True,
            )
        assert result["fan_mode"] == "med"
        assert "HVAC idle" in result["reason"]

    def test_zone_a_emergency_not_blocked_by_idle(self, controller):
        """Zone A: emergency should override idle protection."""
        with patch("time.time", return_value=3600.0):
            result = controller.calculate_decision(
                current_temp=19.0,
                target_temp=20.0,
                vtherm_slope=0.0,
                hvac_mode="heat",
                current_fan="low",
                is_hvac_idle=True,
            )
        assert result["fan_mode"] == "superhigh"
        assert "Emergency" in result["reason"]

    def test_zone_a_setpoint_drop_not_blocked_by_idle(self, controller):
        """Zone A-bis: setpoint drop should work even during idle."""
        with patch("time.time", return_value=3600.0):
            result = controller.calculate_decision(
                current_temp=22.5,
                target_temp=20.0,
                vtherm_slope=0.0,
                hvac_mode="heat",
                current_fan="high",
                is_hvac_idle=True,
            )
        assert result["fan_mode"] == "silent"
        assert "Setpoint drop" in result["reason"]

    def test_zone_b_braking_allowed_during_idle(self, controller):
        """Zone B: braking step-down should work during idle."""
        controller.previous_slope = 0.3
        controller._last_change_time = 3600.0 - (DEFAULT_MIN_INTERVAL * 60 + 60)
        with patch("time.time", return_value=3600.0):
            result = controller.calculate_decision(
                current_temp=19.95,
                target_temp=20.0,
                vtherm_slope=3.0,
                hvac_mode="heat",
                current_fan="high",
                is_hvac_idle=True,
            )
        assert result["fan_mode"] == "med"
        assert "Braking" in result["reason"]

    def test_zone_e_step_down_allowed_during_idle(self, controller):
        """Zone E: over-target step-down should work during idle."""
        controller.previous_slope = 0.3
        controller._last_change_time = 3600.0 - (16 * 60)
        with patch("time.time", return_value=3600.0):
            result = controller.calculate_decision(
                current_temp=20.5,
                target_temp=20.0,
                vtherm_slope=0.3,
                hvac_mode="heat",
                current_fan="high",
                is_hvac_idle=True,
            )
        assert result["fan_mode"] == "med"
        assert "Over-target" in result["reason"]


class TestHvacIdleLearningExclusion:
    """Learning data should be excluded during HVAC idle periods."""

    def test_slope_samples_excluded_during_idle(self, controller):
        """Slope samples should NOT be recorded when compressor is off."""
        initial_samples = controller.learning.slope_sample_count()
        controller._last_change_time = 0.0
        with patch("time.time", return_value=3600.0):
            controller.calculate_decision(
                current_temp=19.8,
                target_temp=20.0,
                vtherm_slope=0.5,
                hvac_mode="heat",
                current_fan="med",
                is_hvac_idle=True,
            )
        assert controller.learning.slope_sample_count() == initial_samples

    def test_slope_samples_collected_when_not_idle(self, controller):
        """Slope samples should be recorded when compressor is running."""
        initial_samples = controller.learning.slope_sample_count()
        controller._last_change_time = 0.0
        with patch("time.time", return_value=3600.0):
            controller.calculate_decision(
                current_temp=19.8,
                target_temp=20.0,
                vtherm_slope=0.5,
                hvac_mode="heat",
                current_fan="med",
                is_hvac_idle=False,
            )
        assert controller.learning.slope_sample_count() > initial_samples

    def test_response_events_excluded_during_idle(self, controller):
        """Response time events should NOT be recorded during idle."""
        controller.previous_slope = 0.0
        controller._last_change_time = 3600.0 - (5 * 60)  # 5 min ago
        initial_events = controller.learning.response_event_count()
        with patch("time.time", return_value=3600.0):
            controller.calculate_decision(
                current_temp=19.8,
                target_temp=20.0,
                vtherm_slope=0.5,
                hvac_mode="heat",
                current_fan="med",
                is_hvac_idle=True,
            )
        assert controller.learning.response_event_count() == initial_events


class TestHvacIdleMpc:
    """MPC shadow should pause during HVAC idle."""

    def test_shadow_pauses_during_hvac_idle(self):
        """Shadow should return Disturbed when compressor is off."""
        ctrl = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        _prime_learning_profiles(ctrl)
        shadow = MPCController(
            learning=ctrl.learning,
            deadband=0.3,
            min_interval=10,
            fan_modes=FAN_MODES,
            enabled=True,
        )

        result = shadow.evaluate(
            current_temp=19.5,
            target_temp=20.0,
            vtherm_slope=0.2,
            hvac_mode="heat",
            current_fan="high",
            live_decision_fan="high",
            is_window_open=False,
            is_defrost_active=False,
            is_hvac_idle=True,
            minutes_since_change=12.0,
        )

        assert result["mpc_status"] == "Disturbed"
        assert result["mpc_fan_mode"] == "high"
        assert result["mpc_would_change_now"] == "no"
        assert "HVAC idle" in result["mpc_reason"]

    def test_shadow_active_when_not_idle(self):
        """Shadow should produce a normal recommendation when compressor is running."""
        ctrl = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        _prime_learning_profiles(ctrl)
        shadow = MPCController(
            learning=ctrl.learning,
            deadband=0.3,
            min_interval=10,
            fan_modes=FAN_MODES,
            enabled=True,
        )

        result = shadow.evaluate(
            current_temp=19.5,
            target_temp=20.0,
            vtherm_slope=0.2,
            hvac_mode="heat",
            current_fan="high",
            live_decision_fan="high",
            is_window_open=False,
            is_defrost_active=False,
            is_hvac_idle=False,
            minutes_since_change=12.0,
        )

        assert result["mpc_status"] != "Disturbed"

    def test_shadow_disturbance_bias_decays_during_idle(self):
        """Disturbance bias should decay, not update, during HVAC idle."""
        ctrl = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        _prime_learning_profiles(ctrl)
        shadow = MPCController(
            learning=ctrl.learning,
            deadband=0.3,
            min_interval=10,
            fan_modes=FAN_MODES,
            enabled=True,
        )

        # Prime the disturbance bias with a normal cycle
        shadow.evaluate(
            current_temp=19.5,
            target_temp=20.0,
            vtherm_slope=0.5,
            hvac_mode="heat",
            current_fan="med",
            live_decision_fan="med",
            is_window_open=False,
            minutes_since_change=20.0,
        )
        bias_before = shadow._disturbance_bias

        # HVAC idle cycle — should NOT poison the bias
        shadow.evaluate(
            current_temp=19.5,
            target_temp=20.0,
            vtherm_slope=-0.5,
            hvac_mode="heat",
            current_fan="med",
            live_decision_fan="med",
            is_window_open=False,
            is_hvac_idle=True,
            minutes_since_change=25.0,
        )
        bias_after = shadow._disturbance_bias

        # Bias should have decayed, not grown from the negative slope residual
        assert abs(bias_after) <= abs(bias_before)


class TestHvacIdleCoolMode:
    """HVAC idle also works in cooling mode."""

    def test_zone_c_blocked_during_idle_cool(self, controller):
        """Zone C (recovery in cool): step-up blocked when compressor is off."""
        with patch("time.time", return_value=3600.0):
            result = controller.calculate_decision(
                current_temp=20.5,
                target_temp=20.0,
                vtherm_slope=0.0,
                hvac_mode="cool",
                current_fan="low",
                is_hvac_idle=True,
            )
        assert result["fan_mode"] == "low"
        assert "HVAC idle" in result["reason"]
