"""Tests for ThermalLearning auto-calibration."""
import pytest
import time as time_mod
from unittest.mock import patch
from custom_components.smart_fan_controller.controller import SmartFanController, ThermalLearning
from custom_components.smart_fan_controller.const import MIN_LIMIT_TIMEOUT
from custom_components.smart_fan_controller.sensor import SmartFanLearnedDeadTimeSensor, SmartFanEffectiveTimeoutSensor


class TestThermalLearning:
    """Test auto-calibration and optimal parameter computation."""

    def test_optimal_limit_timeout_with_median_response_times(self):
        """Test that optimal_limit_timeout uses median to handle outliers."""
        learning = ThermalLearning()

        # Add multiple slope samples to satisfy is_ready() requirements
        # Note: slope sample parameters (fan mode, slope value) don't affect limit_timeout
        for _ in range(250):
            learning.add_slope_sample("medium", 0.3, 0.1)

        # Verify that slope samples alone make the learning ready
        assert learning.is_ready()

        # Add response times with outliers: most in 10-12 range, but some outliers at 30+
        # Median should be 12, mean would be much higher
        response_times = [10, 10, 11, 11, 12, 12, 30, 35, 40]  # median=12, mean≈19.0
        for time in response_times:
            learning.add_response_event(time)

        assert learning.is_ready()

        optimal = learning.compute_optimal_parameters()

        # With median=12, optimal_limit_timeout should be 12 (using direct value, no multiplier)
        # Should NOT be based on mean (≈19.0) - median is more robust to outliers
        assert optimal["limit_timeout"] == 12

    def test_optimal_limit_timeout_uses_observed_response_directly(self):
        """Test that optimal_limit_timeout uses observed response time, floored by MIN_LIMIT_TIMEOUT."""
        learning = ThermalLearning()

        # Add samples (using consistent parameters across tests)
        for _ in range(250):
            learning.add_slope_sample("medium", 0.3, 0.1)

        # Add very fast response times (median=4, below MIN_LIMIT_TIMEOUT=5)
        for time in [3, 4, 4, 5]:
            learning.add_response_event(time)

        optimal = learning.compute_optimal_parameters()

        # With median=4 and MIN_LIMIT_TIMEOUT=5, optimal should be clamped to 5

        assert optimal["limit_timeout"] == max(MIN_LIMIT_TIMEOUT, 4)

    def test_optimal_limit_timeout_with_slow_response(self):
        """Test that optimal_limit_timeout follows observed response even when slow."""
        learning = ThermalLearning()

        # Add samples (using consistent parameters)
        for _ in range(250):
            learning.add_slope_sample("medium", 0.3, 0.1)

        # Add very slow response times
        for time in [18, 19, 20, 25, 30]:
            learning.add_response_event(time)

        optimal = learning.compute_optimal_parameters()

        # With median=20, optimal should be 20 (no max cap applied)
        assert optimal["limit_timeout"] == 20

    def test_optimal_limit_timeout_typical_case(self):
        """Test typical response times in 10-15 minute range."""
        learning = ThermalLearning()

        # Add samples
        for _ in range(250):
            learning.add_slope_sample("medium", 0.3, 0.2)

        # Add typical response times user mentioned: 10-15 minutes
        for time in [10, 11, 12, 13, 14, 15, 14, 13]:
            learning.add_response_event(time)

        optimal = learning.compute_optimal_parameters()

        # With median≈13, optimal should be 13 (direct value, no multiplier)
        assert optimal["limit_timeout"] == 13

    def test_response_time_validation_rejects_unreasonable_values(self):
        """Test that very short (<2 min) or very long (>60 min) response times are filtered."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15
        )

        # Add enough slope samples to make learning ready
        for _ in range(250):
            controller.learning.add_slope_sample("medium", 0.3, 0.1)

        base_time = 1000000.0

        # Test 1: Response time of 1 minute (too short, should be filtered)
        with patch('time.time', return_value=base_time):
            controller.now = base_time
            controller.save_states("medium", "low", 0.5, 0.5, False)  # Fan changes
            controller.confirm_fan_change()  # Confirm: sets _last_change_time = base_time

        events_before = len(controller.learning.response_events)
        with patch('time.time', return_value=base_time + 60):  # +1 minute
            controller.now = base_time + 60
            controller.save_states("medium", "medium", 0.3, 0.3, True)  # Slope changes
        events_after = len(controller.learning.response_events)
        assert events_after == events_before, "1-minute response should be filtered out"

        # Test 2: Response time of 10 minutes (valid, should be recorded)
        with patch('time.time', return_value=base_time + 1000):
            controller.now = base_time + 1000
            controller.save_states("high", "medium", 0.6, 0.6, False)  # Fan changes
            controller.confirm_fan_change()  # Confirm: sets _last_change_time = base_time + 1000

        events_before = len(controller.learning.response_events)
        with patch('time.time', return_value=base_time + 1000 + 600):  # +10 minutes
            controller.now = base_time + 1000 + 600
            controller.save_states("high", "high", 0.4, 0.4, True)  # Slope changes
        events_after = len(controller.learning.response_events)
        assert events_after == events_before + 1, "10-minute response should be recorded"
        assert controller.learning.response_events[-1][1] == 10.0

        # Test 3: Response time of 65 minutes (too long, should be filtered)
        with patch('time.time', return_value=base_time + 5000):
            controller.now = base_time + 5000
            controller.save_states("low", "high", 0.7, 0.7, False)  # Fan changes
            controller.confirm_fan_change()  # Confirm: sets _last_change_time = base_time + 5000

        events_before = len(controller.learning.response_events)
        with patch('time.time', return_value=base_time + 5000 + 3900):  # +65 minutes
            controller.now = base_time + 5000 + 3900
            controller.save_states("low", "low", 0.2, 0.2, True)  # Slope changes
        events_after = len(controller.learning.response_events)
        assert events_after == events_before, "65-minute response should be filtered out"

    def test_learned_dead_time_sensor_reports_median_response(self):
        """The diagnostic dead-time sensor should expose the median response delay."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
        )
        for response_time in [6.0, 8.0, 8.0, 10.0]:
            controller.learning.add_response_event(response_time)

        sensor = SmartFanLearnedDeadTimeSensor("entry", controller)

        assert sensor.native_value == 8.0

    def test_effective_timeout_sensor_shows_runtime_timeout(self):
        """The effective-timeout sensor should expose dead_time × 1.5 once learning is ready."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
        )
        for _ in range(250):
            controller.learning.add_slope_sample("medium", 0.3, 0.1)
        for response_time in [8.0, 8.0, 9.0]:
            controller.learning.add_response_event(response_time)

        sensor = SmartFanEffectiveTimeoutSensor("entry", controller)

        assert sensor.native_value == 12.0

    def test_set_mode_effective_slope_replaces_samples(self):
        """set_mode_effective_slope should replace existing samples and produce the target slope."""
        learning = ThermalLearning()

        # Add some initial samples for silent/heat
        for _ in range(15):
            learning.add_slope_sample("silent", 0.8, 0.2, hvac_mode="heat")

        assert learning.get_mode_effective_slope("silent", "heat") == pytest.approx(0.8, abs=0.01)

        # Override to a lower value
        learning.set_mode_effective_slope("silent", "heat", 0.15)

        assert learning.get_mode_effective_slope("silent", "heat") == pytest.approx(0.15, abs=0.001)
        assert learning.get_mode_sample_count("silent", "heat") == 10

    def test_set_mode_effective_slope_cool_inverts(self):
        """In cool mode, effective slope sign is inverted vs raw slope."""
        learning = ThermalLearning()

        learning.set_mode_effective_slope("high", "cool", 0.5)

        # effective_slope should be 0.5 (positive = towards target)
        assert learning.get_mode_effective_slope("high", "cool") == pytest.approx(0.5, abs=0.001)

    def test_set_mode_effective_slope_preserves_other_profiles(self):
        """Overriding one profile should not affect other profiles."""
        learning = ThermalLearning()

        for _ in range(15):
            learning.add_slope_sample("silent", 0.8, 0.2, hvac_mode="heat")
        for _ in range(15):
            learning.add_slope_sample("med", 0.5, 0.2, hvac_mode="heat")

        learning.set_mode_effective_slope("silent", "heat", 0.15)

        assert learning.get_mode_effective_slope("silent", "heat") == pytest.approx(0.15, abs=0.001)
        assert learning.get_mode_effective_slope("med", "heat") == pytest.approx(0.5, abs=0.01)

    def test_median_resists_outliers(self):
        """Median should resist a single extreme outlier sample."""
        learning = ThermalLearning()

        # 12 normal samples at ~0.15, plus 3 outlier at 1.29 (inertia contamination)
        for _ in range(12):
            learning.add_slope_sample("silent", 0.15, 0.2, hvac_mode="heat")
        for _ in range(3):
            learning.add_slope_sample("silent", 1.29, 0.2, hvac_mode="heat")

        slope = learning.get_mode_effective_slope("silent", "heat")
        # Median of [0.15]*12 + [1.29]*3 = 0.15 (most values are 0.15)
        assert slope is not None
        assert slope == pytest.approx(0.15, abs=0.01)

    def test_setpoint_drop_cooldown_blocks_learning(self):
        """Samples should be blocked for 30 min after a setpoint-drop event."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
        )

        now = time_mod.time()

        # Trigger a setpoint drop (error < -1°C: target moved away from current)
        # In heat mode: error = target - current = 18.0 - 20.5 = -2.5
        with patch("time.time", return_value=now):
            controller.last_change_time = now - 1800
            decision = controller.calculate_decision(
                current_temp=20.5,
                target_temp=18.0,
                vtherm_slope=0.5,
                hvac_mode="heat",
                current_fan="medium",
            )
        assert "Setpoint drop" in decision.get("reason", "")

        # 15 min later (within cooldown), ESTABLISHED slope should NOT be learned
        with patch("time.time", return_value=now + 900):
            controller.last_change_time = now - 1800  # ensure ESTABLISHED + min stable
            controller.calculate_decision(
                current_temp=19.5,
                target_temp=20.0,
                vtherm_slope=0.5,
                hvac_mode="heat",
                current_fan="medium",
            )
        assert controller.learning.slope_sample_count() == 0

        # 35 min later (after cooldown), learning should resume
        with patch("time.time", return_value=now + 2100):
            controller.last_change_time = now  # ~35 min ago, ESTABLISHED + min stable
            controller.calculate_decision(
                current_temp=19.5,
                target_temp=20.0,
                vtherm_slope=0.5,
                hvac_mode="heat",
                current_fan="medium",
            )
        assert controller.learning.slope_sample_count() > 0

    def test_min_stable_duration_blocks_early_samples(self):
        """Samples should be blocked when fan mode hasn't been active long enough."""
        controller = SmartFanController(
            fan_modes=["low", "medium", "high"],
            deadband=0.2,
            min_interval=10,
            soft_error=0.3,
            hard_error=0.6,
            limit_timeout=15,
        )

        now = time_mod.time()

        # Set last change to only 16 min ago (ESTABLISHED but < 2 × 10 min dead_time = 20 min)
        with patch("time.time", return_value=now):
            controller.last_change_time = now - 960  # 16 min ago
            controller.calculate_decision(
                current_temp=22.0,
                target_temp=21.0,
                vtherm_slope=0.5,
                hvac_mode="heat",
                current_fan="medium",
            )
        assert controller.learning.slope_sample_count() == 0

        # Set last change to 25 min ago (> 20 min), should learn
        with patch("time.time", return_value=now + 600):
            controller.last_change_time = now - 900  # 25 min ago
            controller.calculate_decision(
                current_temp=22.0,
                target_temp=21.0,
                vtherm_slope=0.5,
                hvac_mode="heat",
                current_fan="medium",
            )
        assert controller.learning.slope_sample_count() > 0
