"""Tests for phase detection, adaptive timeout, descent path, and window filtering."""
import pytest
from unittest.mock import patch

from custom_components.smart_fan_controller.controller import SmartFanController
from custom_components.smart_fan_controller.thermal_learning import ThermalLearning
from custom_components.smart_fan_controller.const import (
    DEFAULT_DEADBAND,
    DEFAULT_MIN_INTERVAL,
    DEFAULT_SOFT_ERROR,
    DEFAULT_HARD_ERROR,
    PHASE_DEAD_TIME,
    PHASE_TRANSIENT,
    PHASE_ESTABLISHED,
    DEFAULT_DEAD_TIME,
    DEAD_TIME_SAFETY_FACTOR,
    MIN_SAMPLES_LEARNING,
)

FAN_MODES = ["low", "medium", "high", "turbo"]
DEFAULT_CONFIG = {
    "deadband": DEFAULT_DEADBAND,
    "min_interval": DEFAULT_MIN_INTERVAL,
    "soft_error": DEFAULT_SOFT_ERROR,
    "hard_error": DEFAULT_HARD_ERROR,
}


@pytest.fixture
def controller():
    return SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)


class TestPhaseDetection:
    """Tests for _detect_phase with default and learned dead times."""

    def test_dead_time_phase_default(self, controller):
        """Within default dead time (10 min), phase should be DEAD_TIME."""
        assert controller._detect_phase(5.0) == PHASE_DEAD_TIME

    def test_transient_phase_default(self, controller):
        """Between dead time and dead_time * 1.5, phase should be TRANSIENT."""
        assert controller._detect_phase(12.0) == PHASE_TRANSIENT

    def test_established_phase_default(self, controller):
        """After dead_time * 1.5, phase should be ESTABLISHED."""
        assert controller._detect_phase(16.0) == PHASE_ESTABLISHED

    def test_phase_boundary_dead_to_transient(self, controller):
        """At exactly the dead time boundary, phase transitions to TRANSIENT."""
        # DEFAULT_DEAD_TIME = 10.0; at 10.0 it's no longer < 10 → TRANSIENT
        assert controller._detect_phase(DEFAULT_DEAD_TIME) == PHASE_TRANSIENT

    def test_phase_boundary_transient_to_established(self, controller):
        """At exactly dead_time * 1.5, phase transitions to ESTABLISHED."""
        threshold = DEFAULT_DEAD_TIME * DEAD_TIME_SAFETY_FACTOR
        assert controller._detect_phase(threshold) == PHASE_ESTABLISHED

    def test_phase_with_learned_dead_time(self):
        """When learning provides a dead time, phases use that value."""
        controller = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        # Populate learning with enough data and response events
        for _ in range(MIN_SAMPLES_LEARNING):
            controller.learning.add_slope_sample("medium", 0.4, 0.2)
        # Dead time of 6 minutes (all events at 6 min)
        for _ in range(5):
            controller.learning.add_response_event(6.0)
        assert controller.learning.is_ready()

        # 4 min < 6 → DEAD_TIME
        assert controller._detect_phase(4.0) == PHASE_DEAD_TIME
        # 7 min → between 6 and 9 → TRANSIENT
        assert controller._detect_phase(7.0) == PHASE_TRANSIENT
        # 10 min → > 9 → ESTABLISHED
        assert controller._detect_phase(10.0) == PHASE_ESTABLISHED


class TestAdaptiveTimeout:
    """Tests for _get_effective_timeout."""

    def test_default_timeout_without_learning(self, controller):
        """Without learning data, timeout falls back to _limit_timeout."""
        assert controller._get_effective_timeout() == controller._limit_timeout

    def test_adaptive_timeout_with_learning(self):
        """With learning data, timeout = max(min_interval, dead_time * 1.5)."""
        controller = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        for _ in range(MIN_SAMPLES_LEARNING):
            controller.learning.add_slope_sample("medium", 0.4, 0.2)
        for _ in range(5):
            controller.learning.add_response_event(8.0)  # dead time = 8 min
        assert controller.learning.is_ready()

        expected = max(DEFAULT_MIN_INTERVAL, 8.0 * DEAD_TIME_SAFETY_FACTOR)  # max(10, 12) = 12
        assert controller._get_effective_timeout() == expected

    def test_adaptive_timeout_floors_to_min_interval(self):
        """When learned dead_time * 1.5 < min_interval, min_interval wins."""
        controller = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        for _ in range(MIN_SAMPLES_LEARNING):
            controller.learning.add_slope_sample("medium", 0.4, 0.2)
        for _ in range(5):
            controller.learning.add_response_event(4.0)  # dead time = 4 min; 4*1.5=6 < 10
        assert controller.learning.is_ready()

        assert controller._get_effective_timeout() == DEFAULT_MIN_INTERVAL

    def test_adaptive_timeout_disabled_when_learning_off(self):
        """When learning is disabled, always use static limit_timeout."""
        controller = SmartFanController(
            fan_modes=FAN_MODES, learning_enabled=False, **DEFAULT_CONFIG
        )
        assert controller._get_effective_timeout() == controller._limit_timeout


class TestDeadTimePatienceInZoneC:
    """Zone C must not boost during the dead-time phase."""

    def test_zone_c_patience_during_dead_time(self):
        """During DEAD_TIME, zone C should report patience, not boost."""
        controller = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        start_time = 1000.0
        controller._last_change_time = start_time
        controller._previous_slope = 0.0

        # 5 minutes after change → DEAD_TIME (< DEFAULT_DEAD_TIME=10)
        with patch("time.time", return_value=start_time + 5 * 60):
            result = controller.calculate_decision(
                current_temp=19.5,  # error = 0.5 > soft_error (0.3)
                target_temp=20.0,
                vtherm_slope=0.0,  # slope unchanged
                hvac_mode="heat",
                current_fan="low",
            )
        assert result["fan_mode"] == "low"
        assert "Patience: Waiting for thermal response" in result["reason"]

    def test_zone_c_boosts_after_dead_time(self):
        """After dead time expires and slope hasn't improved, zone C should boost."""
        controller = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        start_time = 1000.0
        controller._last_change_time = start_time
        controller._previous_slope = 0.0

        # 16 minutes → ESTABLISHED phase, interval_expired
        with patch("time.time", return_value=start_time + 16 * 60):
            result = controller.calculate_decision(
                current_temp=19.5,  # error 0.5 > soft_error
                target_temp=20.0,
                vtherm_slope=0.0,
                hvac_mode="heat",
                current_fan="low",
            )
        assert result["fan_mode"] == "medium"
        assert "recovery" in result["reason"].lower()


class TestDescentPathZoneD:
    """Zone D should allow descent when slope is strongly favorable."""

    def test_descent_with_favorable_slope(self, controller):
        """In zone D, strong favorable slope in established phase → reduce."""
        start_time = 1000.0
        controller._last_change_time = start_time - 20 * 60  # 20 min ago
        controller._previous_slope = 0.5

        with patch("time.time", return_value=start_time):
            result = controller.calculate_decision(
                current_temp=19.9,  # error 0.1 in heat (0 < 0.1 < 0.3)
                target_temp=20.0,
                vtherm_slope=0.8,   # effective_slope = 0.8 > 0.2 threshold
                hvac_mode="heat",
                current_fan="high",
            )
        assert result["fan_mode"] == "medium"
        assert "Strong favorable slope" in result["reason"]

    def test_no_descent_during_dead_time(self):
        """Zone D descent must not fire during dead time, even with favorable slope."""
        controller = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        start_time = 1000.0
        controller._last_change_time = start_time  # just changed
        controller._previous_slope = 0.5

        # 3 min → DEAD_TIME
        with patch("time.time", return_value=start_time + 3 * 60):
            result = controller.calculate_decision(
                current_temp=19.9,
                target_temp=20.0,
                vtherm_slope=0.8,
                hvac_mode="heat",
                current_fan="high",
            )
        # Should still be high; observing inertia (phase is dead time, interval not expired)
        assert result["fan_mode"] == "high"
        assert "Observing inertia" in result["reason"]

    def test_zone_d_proactive_increase_only_in_established(self):
        """Zone D proactive increase ('Stable away from target') requires ESTABLISHED phase."""
        controller = SmartFanController(fan_modes=FAN_MODES, **DEFAULT_CONFIG)
        start_time = 1000.0
        controller._last_change_time = start_time
        controller._previous_slope = -0.01

        # 12 minutes → TRANSIENT (between 10 and 15), effective_timeout = 15
        with patch("time.time", return_value=start_time + 12 * 60):
            result = controller.calculate_decision(
                current_temp=19.9,
                target_temp=20.0,
                vtherm_slope=-0.0,  # stable, no drift
                hvac_mode="heat",
                current_fan="low",
            )
        # TRANSIENT phase + interval not expired → observing inertia
        assert result["fan_mode"] == "low"
        assert "Observing inertia" in result["reason"]


class TestWindowOpenFiltering:
    """Window-open state should prevent learning data collection."""

    def test_learning_skips_samples_when_window_open(self, controller):
        """With is_window_open=True, no learning samples should be collected."""
        result = controller.calculate_decision(
            current_temp=22.0,
            target_temp=21.0,
            vtherm_slope=0.5,
            hvac_mode="heat",
            current_fan="medium",
            is_window_open=True,
        )
        assert controller.learning.slope_sample_count() == 0

    def test_learning_collects_when_window_closed(self, controller):
        """With is_window_open=False (default), samples are collected normally."""
        result = controller.calculate_decision(
            current_temp=22.0,
            target_temp=21.0,
            vtherm_slope=0.5,
            hvac_mode="heat",
            current_fan="medium",
            is_window_open=False,
        )
        assert controller.learning.slope_sample_count() > 0

    def test_window_open_does_not_affect_decision(self, controller):
        """Window-open only affects learning, not the decision itself."""
        result = controller.calculate_decision(
            current_temp=19.0,
            target_temp=20.0,
            vtherm_slope=0.0,
            hvac_mode="heat",
            current_fan="low",
            is_window_open=True,
        )
        # Emergency still fires even with window open
        assert result["fan_mode"] == "turbo"
        assert "Emergency" in result["reason"]


class TestLinearProjectionClamp:
    """Tests for the clamped linear temperature projection."""

    def test_linear_projection_positive_slope(self, controller):
        """Positive slope projects temperature increase."""
        controller._previous_slope = 0.0
        proj = controller.compute_temperature_projection(20.0, 1.2)
        # 20.0 + 1.2 * (10/60) = 20.0 + 0.2 = 20.2
        assert proj == pytest.approx(20.2, abs=0.01)

    def test_linear_projection_negative_slope(self, controller):
        """Negative slope projects temperature decrease."""
        controller._previous_slope = 0.0
        proj = controller.compute_temperature_projection(20.0, -1.8)
        # 20.0 + (-1.8) * (10/60) = 20.0 - 0.3 = 19.7
        assert proj == pytest.approx(19.7, abs=0.01)

    def test_projection_clamp_maximum(self, controller):
        """Extreme positive slope is clamped to +2°C."""
        proj = controller.compute_temperature_projection(20.0, 30.0)
        # Raw: 20.0 + 30 * 0.1667 = 25.0 → clamped to 22.0
        assert proj == 22.0

    def test_projection_clamp_minimum(self, controller):
        """Extreme negative slope is clamped to -2°C."""
        proj = controller.compute_temperature_projection(20.0, -30.0)
        # Raw: 20.0 - 5.0 = 15.0 → clamped to 18.0
        assert proj == 18.0


class TestPerModeLearningProfile:
    """Tests for per-fan-mode effective slope profiles."""

    def test_mode_slope_heating(self):
        """In heating, effective slope = raw slope (positive = towards target)."""
        learning = ThermalLearning()
        for _ in range(15):
            learning.add_slope_sample("medium", 0.5, 0.3, hvac_mode="heat")
        result = learning.get_mode_effective_slope("medium", "heat")
        assert result is not None
        assert result == pytest.approx(0.5, abs=0.01)

    def test_mode_slope_cooling(self):
        """In cooling, effective slope = -raw slope (negative raw → positive effective)."""
        learning = ThermalLearning()
        for _ in range(15):
            learning.add_slope_sample("high", -0.8, 0.3, hvac_mode="cool")
        result = learning.get_mode_effective_slope("high", "cool")
        assert result is not None
        assert result == pytest.approx(0.8, abs=0.01)

    def test_mode_slope_insufficient_samples(self):
        """Returns None when fewer than MIN_MODE_PROFILE_SAMPLES."""
        learning = ThermalLearning()
        for _ in range(5):
            learning.add_slope_sample("low", 0.3, 0.2, hvac_mode="heat")
        assert learning.get_mode_effective_slope("low", "heat") is None

    def test_mode_slope_filters_by_hvac(self):
        """Profiles for 'heat' and 'cool' are independent."""
        learning = ThermalLearning()
        for _ in range(15):
            learning.add_slope_sample("medium", 0.5, 0.3, hvac_mode="heat")
        for _ in range(15):
            learning.add_slope_sample("medium", -0.6, 0.3, hvac_mode="cool")

        heat_slope = learning.get_mode_effective_slope("medium", "heat")
        cool_slope = learning.get_mode_effective_slope("medium", "cool")

        assert heat_slope == pytest.approx(0.5, abs=0.01)
        assert cool_slope == pytest.approx(0.6, abs=0.01)

    def test_mode_slope_filters_window_open(self):
        """Samples with is_window_open=True are not included in profiles."""
        learning = ThermalLearning()
        for _ in range(15):
            learning.add_slope_sample("medium", 0.5, 0.3, hvac_mode="heat", is_window_open=True)
        assert learning.get_mode_effective_slope("medium", "heat") is None

    def test_mode_slope_filters_setpoint_drop(self):
        """Night mode samples (error < -1°C) are not included in profiles."""
        learning = ThermalLearning()
        for _ in range(15):
            learning.add_slope_sample("medium", -0.5, -2.0, hvac_mode="heat")
        assert learning.get_mode_effective_slope("medium", "heat") is None


class TestBackwardCompatibility:
    """Test that old 3-tuple slope samples are migrated correctly."""

    def test_from_dict_old_format(self):
        """Old data with 3-tuples should be upgraded to 4-tuples with 'unknown' hvac_mode."""
        import time
        ts = time.time() - 3600  # 1 hour ago
        old_data = {
            "slope_samples": [(ts, "medium", 0.5), (ts, "high", 0.8)],
            "response_events": [(ts, 12.5)],
            "slope_count": 2,
            "slope_mean": 0.65,
            "slope_M2": 0.045,
            "slope_max": 0.8,
        }
        restored = ThermalLearning.from_dict(old_data)

        assert len(restored._slope_samples) == 2
        # Each sample should now be a 4-tuple
        for sample in restored._slope_samples:
            assert len(sample) == 4
            assert sample[3] == "unknown"

    def test_from_dict_new_format(self):
        """New data with 4-tuples round-trips correctly."""
        import time
        ts = time.time() - 3600
        new_data = {
            "slope_samples": [(ts, "medium", 0.5, "heat"), (ts, "high", -0.8, "cool")],
            "response_events": [(ts, 12.5)],
            "slope_count": 2,
            "slope_mean": 0.65,
            "slope_M2": 0.045,
            "slope_max": 0.8,
        }
        restored = ThermalLearning.from_dict(new_data)

        assert len(restored._slope_samples) == 2
        assert restored._slope_samples[0][3] == "heat"
        assert restored._slope_samples[1][3] == "cool"

    def test_round_trip_preserves_hvac_mode(self):
        """to_dict → from_dict preserves the 4-tuple format."""
        learning = ThermalLearning()
        learning.add_slope_sample("medium", 0.5, 0.3, hvac_mode="heat")
        learning.add_slope_sample("high", -0.8, 0.3, hvac_mode="cool")

        data = learning.to_dict()
        restored = ThermalLearning.from_dict(data)

        for original, restored_s in zip(learning._slope_samples, restored._slope_samples):
            assert len(restored_s) == 4
            assert original[3] == restored_s[3]  # hvac_mode preserved
