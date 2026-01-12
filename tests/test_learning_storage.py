"""Tests for adaptive learning storage system."""
import pytest
import os
import json
import tempfile
from datetime import datetime

from custom_components.smart_fan_controller.learning_storage import LearningStorage


@pytest.fixture
def temp_dir():
    """Fixture to provide a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def storage(temp_dir):
    """Fixture to provide a clean LearningStorage instance."""
    return LearningStorage(temp_dir, "climate.test_entity")


class TestLearningStorage:
    """Test suite for LearningStorage class."""

    def test_initialization(self, storage):
        """Test storage initialization with default state."""
        state = storage.get_state()
        
        assert state["version"] == "1.0"
        assert state["climate_entity_id"] == "climate.test_entity"
        assert "fan_mode_profiles" in state
        assert "thermal_parameters" in state
        assert "learning_metadata" in state
        assert state["learning_metadata"]["total_decisions"] == 0
        assert state["learning_metadata"]["learning_confidence"] == 0.0

    def test_save_and_load(self, storage, temp_dir):
        """Test saving and loading learning state."""
        # Update some values
        storage.update_fan_mode_profile(
            fan_mode="high",
            slope_change=0.5,
            effectiveness_score=0.8
        )
        storage.update_metadata(decision_made=True, prediction_successful=True)
        
        # Save
        assert storage.save() is True
        
        # Create new storage instance and load
        new_storage = LearningStorage(temp_dir, "climate.test_entity")
        assert new_storage.load() is True
        
        # Verify loaded data
        profile = new_storage.get_fan_mode_profile("high")
        assert profile["activation_count"] == 1
        assert profile["avg_slope_change"] == 0.5
        assert profile["effectiveness_score"] == 0.8
        
        metadata = new_storage.get_state()["learning_metadata"]
        assert metadata["total_decisions"] == 1
        assert metadata["successful_predictions"] == 1

    def test_fan_mode_profile_initialization(self, storage):
        """Test fan mode profile initialization."""
        storage.initialize_fan_mode_profile("medium")
        
        profile = storage.get_fan_mode_profile("medium")
        assert profile["avg_slope_change"] == 0.0
        assert profile["avg_response_time"] == 0.0
        assert profile["effectiveness_score"] == 0.5
        assert profile["activation_count"] == 0

    def test_fan_mode_profile_update(self, storage):
        """Test updating fan mode profile with exponential moving average."""
        # First update
        storage.update_fan_mode_profile(
            fan_mode="low",
            slope_change=1.0,
            effectiveness_score=0.9
        )
        
        profile = storage.get_fan_mode_profile("low")
        # With alpha=0.1: 0.1 * 1.0 + 0.9 * 0.0 = 0.1
        assert profile["avg_slope_change"] == pytest.approx(0.1)
        # With alpha=0.1: 0.1 * 0.9 + 0.9 * 0.5 = 0.54
        assert profile["effectiveness_score"] == pytest.approx(0.54)
        assert profile["activation_count"] == 1
        
        # Second update
        storage.update_fan_mode_profile(
            fan_mode="low",
            slope_change=0.5,
            effectiveness_score=0.8
        )
        
        profile = storage.get_fan_mode_profile("low")
        # 0.1 * 0.5 + 0.9 * 0.1 = 0.14
        assert profile["avg_slope_change"] == pytest.approx(0.14)
        # 0.1 * 0.8 + 0.9 * 0.54 = 0.566
        assert profile["effectiveness_score"] == pytest.approx(0.566)
        assert profile["activation_count"] == 2

    def test_overshoot_undershoot_tracking(self, storage):
        """Test tracking of overshoot and undershoot events."""
        storage.update_fan_mode_profile(
            fan_mode="turbo",
            overshoot=True
        )
        storage.update_fan_mode_profile(
            fan_mode="turbo",
            undershoot=True
        )
        storage.update_fan_mode_profile(
            fan_mode="turbo",
            overshoot=True
        )
        
        profile = storage.get_fan_mode_profile("turbo")
        assert profile["total_overshoot_events"] == 2
        assert profile["total_undershoot_events"] == 1
        assert profile["activation_count"] == 3

    def test_thermal_parameters_update(self, storage):
        """Test updating thermal parameters."""
        storage.update_thermal_parameters(
            thermal_inertia=0.8,
            response_time=250.0
        )
        
        params = storage.get_thermal_parameters()
        # With alpha=0.05: 0.05 * 0.8 + 0.95 * 0.5 = 0.515
        assert params["learned_thermal_inertia"] == pytest.approx(0.515)
        # With alpha=0.05: 0.05 * 250 + 0.95 * 300 = 297.5
        assert params["avg_response_time"] == pytest.approx(297.5)

    def test_metadata_update(self, storage):
        """Test metadata updates and confidence calculation."""
        # No decisions yet
        assert storage.get_learning_confidence() == 0.0
        
        # Add some decisions
        for i in range(50):
            storage.update_metadata(decision_made=True)
            if i % 2 == 0:  # 50% success rate
                storage.update_metadata(prediction_successful=True)
        
        metadata = storage.get_state()["learning_metadata"]
        assert metadata["total_decisions"] == 50
        assert metadata["successful_predictions"] == 25
        
        # Confidence = (observation_conf * 0.5 + accuracy_conf * 0.5)
        # observation_conf = 50/100 = 0.5
        # accuracy_conf = 25/50 = 0.5
        # confidence = 0.5 * 0.5 + 0.5 * 0.5 = 0.5
        assert storage.get_learning_confidence() == pytest.approx(0.5)

    def test_learning_confidence_scaling(self, storage):
        """Test learning confidence scaling with observations."""
        # Low observations, high accuracy
        for i in range(20):
            storage.update_metadata(decision_made=True, prediction_successful=True)
        
        # observation_conf = 20/100 = 0.2
        # accuracy_conf = 20/20 = 1.0
        # confidence = 0.2 * 0.5 + 1.0 * 0.5 = 0.6
        assert storage.get_learning_confidence() == pytest.approx(0.6)
        
        # Add more observations with lower accuracy
        for i in range(80):
            storage.update_metadata(decision_made=True)
            if i % 2 == 0:
                storage.update_metadata(prediction_successful=True)
        
        # observation_conf = 100/100 = 1.0
        # accuracy_conf = (20 + 40)/100 = 0.6
        # confidence = 1.0 * 0.5 + 0.6 * 0.5 = 0.8
        assert storage.get_learning_confidence() == pytest.approx(0.8)

    def test_reset_full(self, storage):
        """Test full learning reset."""
        # Add some data
        storage.update_fan_mode_profile("high", slope_change=0.5)
        storage.update_metadata(decision_made=True)
        
        # Reset
        storage.reset_learning(full_reset=True)
        
        # Verify reset
        state = storage.get_state()
        assert len(state["fan_mode_profiles"]) == 0
        assert state["learning_metadata"]["total_decisions"] == 0
        assert storage.get_learning_confidence() == 0.0

    def test_reset_fan_mode(self, storage):
        """Test resetting specific fan mode."""
        # Add data for multiple modes
        storage.update_fan_mode_profile("low", slope_change=0.3)
        storage.update_fan_mode_profile("high", slope_change=0.7)
        
        # Reset only high
        storage.reset_learning(full_reset=False, fan_mode="high")
        
        # Verify
        state = storage.get_state()
        assert "low" in state["fan_mode_profiles"]
        assert "high" not in state["fan_mode_profiles"]

    def test_storage_file_path(self, storage):
        """Test storage file path generation."""
        filename = storage._get_storage_filename()
        assert "smart_fan_learning_climate_test_entity.json" in filename

    def test_corrupt_file_handling(self, temp_dir):
        """Test handling of corrupt learning file."""
        # Create corrupt file
        storage = LearningStorage(temp_dir, "climate.test")
        filepath = storage._storage_file
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            f.write("{ corrupt json }")
        
        # Should return False and use defaults
        assert storage.load() is False
        assert storage.get_learning_confidence() == 0.0

    def test_missing_file_handling(self, temp_dir):
        """Test handling of missing learning file."""
        storage = LearningStorage(temp_dir, "climate.new")
        
        # Should return False but not crash
        assert storage.load() is False
        assert storage.get_state() is not None

    def test_atomic_save(self, storage):
        """Test atomic save operation."""
        storage.update_metadata(decision_made=True)
        
        # Save should create temp file and rename
        assert storage.save() is True
        
        # Verify file exists
        assert os.path.exists(storage._storage_file)
        
        # Verify temp file is cleaned up
        temp_file = storage._storage_file + ".tmp"
        assert not os.path.exists(temp_file)

    def test_backward_compatibility(self, temp_dir):
        """Test loading old version with merge."""
        # Create old-style state with missing keys
        old_state = {
            "version": "1.0",
            "climate_entity_id": "climate.test",
            "fan_mode_profiles": {"low": {"activation_count": 5}},
            "thermal_parameters": {"learned_thermal_inertia": 0.6},
            "learning_metadata": {"total_decisions": 10}
        }
        
        storage = LearningStorage(temp_dir, "climate.test")
        filepath = storage._storage_file
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(old_state, f)
        
        # Load and verify merge
        assert storage.load() is True
        state = storage.get_state()
        
        # Old values preserved
        assert state["learning_metadata"]["total_decisions"] == 10
        
        # New defaults added
        assert "learning_confidence" in state["learning_metadata"]
        assert "avg_response_time" in state["thermal_parameters"]
