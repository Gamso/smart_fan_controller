"""Tests for ThermalLearning auto-calibration."""
import pytest
from custom_components.smart_fan_controller.thermal_learning import ThermalLearning
from custom_components.smart_fan_controller.mpc_controller import MPCController
from custom_components.smart_fan_controller.const import MIN_LIMIT_TIMEOUT
from custom_components.smart_fan_controller.sensor import SmartFanLearnedDeadTimeSensor, SmartFanEffectiveTimeoutSensor

FAN_MODES = ["low", "medium", "high"]


def _build_mpc(learning: ThermalLearning) -> MPCController:
    """Build a minimal MPCController for sensor tests."""
    return MPCController(
        fan_modes=FAN_MODES,
        learning=learning,
        deadband=0.2,
        min_interval=10,
        limit_timeout=15,
    )


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

    def test_learned_dead_time_sensor_reports_median_response(self):
        """The diagnostic dead-time sensor should expose the median response delay."""
        learning = ThermalLearning()
        for response_time in [6.0, 8.0, 8.0, 10.0]:
            learning.add_response_event(response_time)
        mpc = _build_mpc(learning)

        sensor = SmartFanLearnedDeadTimeSensor("entry", mpc)

        assert sensor.native_value == 8.0

    def test_effective_timeout_sensor_shows_runtime_timeout(self):
        """The effective-timeout sensor should expose dead_time × 1.5 once learning is ready."""
        learning = ThermalLearning()
        for _ in range(250):
            learning.add_slope_sample("medium", 0.3, 0.1)
        for response_time in [8.0, 8.0, 9.0]:
            learning.add_response_event(response_time)
        mpc = _build_mpc(learning)

        sensor = SmartFanEffectiveTimeoutSensor("entry", mpc)

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
