"""Tests for adaptive learning system."""
import pytest
import tempfile
from unittest.mock import MagicMock, patch
import time

from custom_components.smart_fan_controller.learning_storage import LearningStorage
from custom_components.smart_fan_controller.adaptive_learning import AdaptiveLearning


@pytest.fixture
def temp_dir():
    """Fixture to provide a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def storage(temp_dir):
    """Fixture to provide a clean LearningStorage instance."""
    return LearningStorage(temp_dir, "climate.test")


@pytest.fixture
def adaptive_learning(storage):
    """Fixture to provide an AdaptiveLearning instance."""
    return AdaptiveLearning(storage, enable_learning=True, learning_rate=0.1)


class TestAdaptiveLearning:
    """Test suite for AdaptiveLearning class."""

    def test_initialization(self, adaptive_learning):
        """Test adaptive learning initialization."""
        assert adaptive_learning._enable_learning is True
        assert adaptive_learning._learning_rate == 0.1
        assert adaptive_learning._last_temp is None
        assert adaptive_learning._last_slope is None

    def test_adaptive_parameters_disabled(self, storage):
        """Test that static parameters are used when learning is disabled."""
        learning = AdaptiveLearning(storage, enable_learning=False)
        
        params = learning.get_adaptive_parameters(
            static_deadband=0.2,
            static_soft_error=0.3,
            static_hard_error=0.6,
            static_min_interval=10,
            static_projected_error=0.5
        )
        
        assert params["deadband"] == 0.2
        assert params["soft_error"] == 0.3
        assert params["hard_error"] == 0.6
        assert params["min_interval"] == 10
        assert params["projected_error_threshold"] == 0.5

    def test_adaptive_parameters_low_confidence(self, adaptive_learning):
        """Test parameter blending with low confidence (<0.3)."""
        # Confidence is 0.0 initially
        params = adaptive_learning.get_adaptive_parameters(
            static_deadband=0.2,
            static_soft_error=0.3,
            static_hard_error=0.6,
            static_min_interval=10,
            static_projected_error=0.5
        )
        
        # Should use static parameters
        assert params["deadband"] == 0.2
        assert params["soft_error"] == 0.3
        assert params["hard_error"] == 0.6

    def test_adaptive_parameters_medium_confidence(self, storage):
        """Test parameter blending with medium confidence (0.3-0.7)."""
        # Setup storage with medium confidence (0.5)
        for i in range(50):
            storage.update_metadata(decision_made=True)
            if i % 2 == 0:
                storage.update_metadata(prediction_successful=True)
        
        # Confidence should be ~0.5
        assert storage.get_learning_confidence() == pytest.approx(0.5)
        
        # Set learned thermal inertia
        storage.update_thermal_parameters(thermal_inertia=1.0)
        
        learning = AdaptiveLearning(storage, enable_learning=True)
        
        params = learning.get_adaptive_parameters(
            static_deadband=0.2,
            static_soft_error=0.3,
            static_hard_error=0.6,
            static_min_interval=10,
            static_projected_error=0.5
        )
        
        # With confidence 0.5, blend_factor = (0.5 - 0.3) / 0.4 = 0.5
        # Parameters should be blend of static and learned
        # learned_deadband = 1.0 * 0.4 = 0.4
        # result = 0.2 * 0.5 + 0.4 * 0.5 = 0.3
        assert params["deadband"] == pytest.approx(0.3, abs=0.05)

    def test_adaptive_parameters_high_confidence(self, storage):
        """Test parameter usage with high confidence (>0.7)."""
        # Setup storage with high confidence
        for i in range(100):
            storage.update_metadata(decision_made=True, prediction_successful=True)
        
        # Confidence should be ~0.75
        assert storage.get_learning_confidence() >= 0.7
        
        # Set learned thermal inertia
        storage.update_thermal_parameters(thermal_inertia=1.0)
        
        learning = AdaptiveLearning(storage, enable_learning=True)
        
        params = learning.get_adaptive_parameters(
            static_deadband=0.2,
            static_soft_error=0.3,
            static_hard_error=0.6,
            static_min_interval=10,
            static_projected_error=0.5
        )
        
        # Should mostly use learned parameters
        # learned_deadband = 1.0 * 0.4 = 0.4
        assert params["deadband"] > 0.3  # Closer to learned value

    def test_observe_decision_initialization(self, adaptive_learning):
        """Test observing first decision."""
        adaptive_learning.observe_decision(
            current_temp=20.0,
            target_temp=20.0,
            current_slope=0.5,
            current_fan="low",
            new_fan="low",
            hvac_mode="heat",
            decision_reason="Stable"
        )
        
        assert adaptive_learning._last_temp == 20.0
        assert adaptive_learning._last_slope == 0.5
        assert adaptive_learning._last_fan_mode == "low"

    def test_observe_decision_fan_change(self, adaptive_learning):
        """Test observing fan mode change."""
        current_time = time.time()
        
        with patch('time.time', return_value=current_time):
            adaptive_learning.observe_decision(
                current_temp=19.5,
                target_temp=20.0,
                current_slope=0.0,
                current_fan="low",
                new_fan="medium",
                hvac_mode="heat",
                decision_reason="Recovery"
            )
        
        assert adaptive_learning._fan_change_time == current_time
        assert adaptive_learning._temp_at_fan_change == 19.5
        assert adaptive_learning._slope_at_fan_change == 0.0

    def test_learn_from_observation(self, adaptive_learning):
        """Test learning from observation period."""
        current_time = time.time()
        
        # First observation
        with patch('time.time', return_value=current_time):
            adaptive_learning.observe_decision(
                current_temp=19.5,
                target_temp=20.0,
                current_slope=-0.2,
                current_fan="low",
                new_fan="low",
                hvac_mode="heat",
                decision_reason="Stable"
            )
        
        # Second observation after 2+ minutes (enough for learning)
        with patch('time.time', return_value=current_time + 150):
            adaptive_learning.observe_decision(
                current_temp=19.8,
                target_temp=20.0,
                current_slope=0.5,
                current_fan="low",
                new_fan="medium",
                hvac_mode="heat",
                decision_reason="Recovery"
            )
        
        # Check that fan mode profile was updated
        profile = adaptive_learning._storage.get_fan_mode_profile("low")
        assert profile["activation_count"] == 1
        assert profile["avg_slope_change"] != 0.0

    def test_learn_from_observation_moving_toward_target(self, adaptive_learning):
        """Test learning when moving toward target."""
        current_time = time.time()
        
        # Start: below target with negative slope
        with patch('time.time', return_value=current_time):
            adaptive_learning.observe_decision(
                current_temp=19.0,
                target_temp=20.0,
                current_slope=-0.5,
                current_fan="medium",
                new_fan="medium",
                hvac_mode="heat",
                decision_reason="Recovery"
            )
        
        # Later: closer to target with improved slope
        with patch('time.time', return_value=current_time + 150):
            adaptive_learning.observe_decision(
                current_temp=19.7,
                target_temp=20.0,
                current_slope=1.0,
                current_fan="medium",
                new_fan="medium",
                hvac_mode="heat",
                decision_reason="Stable"
            )
        
        # Should record successful prediction
        profile = adaptive_learning._storage.get_fan_mode_profile("medium")
        assert profile["effectiveness_score"] > 0.5

    def test_learn_overshoot_detection_heat(self, adaptive_learning):
        """Test overshoot detection in heat mode."""
        current_time = time.time()
        
        # Start near target
        with patch('time.time', return_value=current_time):
            adaptive_learning.observe_decision(
                current_temp=19.9,
                target_temp=20.0,
                current_slope=0.8,
                current_fan="high",
                new_fan="high",
                hvac_mode="heat",
                decision_reason="Stable"
            )
        
        # Later: overshot target
        with patch('time.time', return_value=current_time + 150):
            adaptive_learning.observe_decision(
                current_temp=20.5,  # Overshoot by 0.5
                target_temp=20.0,
                current_slope=0.2,
                current_fan="high",
                new_fan="medium",
                hvac_mode="heat",
                decision_reason="Over-target"
            )
        
        # Should record overshoot event
        profile = adaptive_learning._storage.get_fan_mode_profile("high")
        assert profile["total_overshoot_events"] == 1

    def test_learn_overshoot_detection_cool(self, adaptive_learning):
        """Test overshoot detection in cool mode."""
        current_time = time.time()
        
        # Start near target
        with patch('time.time', return_value=current_time):
            adaptive_learning.observe_decision(
                current_temp=20.1,
                target_temp=20.0,
                current_slope=-0.8,
                current_fan="high",
                new_fan="high",
                hvac_mode="cool",
                decision_reason="Stable"
            )
        
        # Later: overshot target (went too cold)
        with patch('time.time', return_value=current_time + 150):
            adaptive_learning.observe_decision(
                current_temp=19.5,  # Overshoot by 0.5
                target_temp=20.0,
                current_slope=-0.2,
                current_fan="high",
                new_fan="medium",
                hvac_mode="cool",
                decision_reason="Over-target"
            )
        
        # Should record overshoot event
        profile = adaptive_learning._storage.get_fan_mode_profile("high")
        assert profile["total_overshoot_events"] == 1

    def test_fan_mode_effectiveness(self, adaptive_learning):
        """Test getting fan mode effectiveness."""
        # Update effectiveness
        adaptive_learning._storage.update_fan_mode_profile(
            fan_mode="turbo",
            effectiveness_score=0.9
        )
        
        effectiveness = adaptive_learning.get_fan_mode_effectiveness("turbo")
        assert effectiveness > 0.5

    def test_recommended_fan_mode_low_confidence(self, adaptive_learning):
        """Test that no recommendation is given with low confidence."""
        recommendation = adaptive_learning.get_recommended_fan_mode(
            available_modes=["low", "medium", "high"],
            current_error=0.5,
            current_slope=-0.2,
            hvac_mode="heat"
        )
        
        # Should return None with low confidence
        assert recommendation is None

    def test_recommended_fan_mode_high_confidence(self, storage):
        """Test fan mode recommendation with high confidence."""
        # Build up confidence
        for i in range(100):
            storage.update_metadata(decision_made=True, prediction_successful=True)
        
        # Add profiles for modes
        storage.update_fan_mode_profile("low", effectiveness_score=0.6)
        storage.update_fan_mode_profile("low")  # Increment activation
        storage.update_fan_mode_profile("low")
        storage.update_fan_mode_profile("low")
        storage.update_fan_mode_profile("low")
        
        storage.update_fan_mode_profile("high", effectiveness_score=0.9)
        storage.update_fan_mode_profile("high")
        storage.update_fan_mode_profile("high")
        storage.update_fan_mode_profile("high")
        storage.update_fan_mode_profile("high")
        
        learning = AdaptiveLearning(storage, enable_learning=True)
        
        recommendation = learning.get_recommended_fan_mode(
            available_modes=["low", "high"],
            current_error=0.5,
            current_slope=-0.2,
            hvac_mode="heat"
        )
        
        # Should recommend "high" as it has better effectiveness
        assert recommendation == "high"

    def test_recommended_fan_mode_insufficient_data(self, storage):
        """Test that no recommendation is given without sufficient activation data."""
        # High confidence but insufficient activations
        for i in range(100):
            storage.update_metadata(decision_made=True, prediction_successful=True)
        
        # Only 2 activations (need 5+)
        storage.update_fan_mode_profile("low", effectiveness_score=0.9)
        storage.update_fan_mode_profile("low")
        
        learning = AdaptiveLearning(storage, enable_learning=True)
        
        recommendation = learning.get_recommended_fan_mode(
            available_modes=["low", "medium"],
            current_error=0.5,
            current_slope=-0.2,
            hvac_mode="heat"
        )
        
        # Should return None without sufficient data
        assert recommendation is None

    def test_learning_info(self, adaptive_learning):
        """Test getting learning information."""
        # Add some learning data
        adaptive_learning._storage.update_fan_mode_profile(
            "low", effectiveness_score=0.7
        )
        adaptive_learning._storage.update_metadata(decision_made=True)
        
        info = adaptive_learning.get_learning_info()
        
        assert info["enabled"] is True
        assert info["confidence"] >= 0.0
        assert info["total_decisions"] == 1
        assert "low" in info["fan_modes"]
        assert info["fan_modes"]["low"]["effectiveness"] == 0.7

    def test_learning_disabled_no_observation(self, storage):
        """Test that observations are skipped when learning is disabled."""
        learning = AdaptiveLearning(storage, enable_learning=False)
        
        learning.observe_decision(
            current_temp=20.0,
            target_temp=20.0,
            current_slope=0.5,
            current_fan="low",
            new_fan="medium",
            hvac_mode="heat",
            decision_reason="Test"
        )
        
        # No learning should have occurred
        assert storage.get_state()["learning_metadata"]["total_decisions"] == 0

    def test_thermal_inertia_learning(self, adaptive_learning):
        """Test learning of thermal inertia."""
        current_time = time.time()
        
        # Observation 1: significant error
        with patch('time.time', return_value=current_time):
            adaptive_learning.observe_decision(
                current_temp=19.0,
                target_temp=20.0,
                current_slope=0.0,
                current_fan="medium",
                new_fan="medium",
                hvac_mode="heat",
                decision_reason="Recovery"
            )
        
        # Observation 2: after 150 seconds with slow improvement
        with patch('time.time', return_value=current_time + 150):
            adaptive_learning.observe_decision(
                current_temp=19.2,  # Only 0.2°C change
                target_temp=20.0,
                current_slope=0.3,
                current_fan="medium",
                new_fan="medium",
                hvac_mode="heat",
                decision_reason="Recovery"
            )
        
        # Thermal inertia should have been updated
        params = adaptive_learning._storage.get_thermal_parameters()
        # High inertia expected (slow response)
        assert params["learned_thermal_inertia"] > 0.5
