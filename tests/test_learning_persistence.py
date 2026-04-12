"""Tests for learning data persistence functionality."""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.smart_fan_controller import async_setup_entry
from custom_components.smart_fan_controller.thermal_learning import ThermalLearning
from custom_components.smart_fan_controller.const import (
    DOMAIN,
    CONF_CLIMATE_ENTITY,
    CONF_DEADBAND,
    DEFAULT_DEADBAND,
)


class TestLearningPersistence:
    """Test learning data serialization and persistence."""

    def test_learning_to_dict_serialization(self):
        """Test that learning data can be serialized to dict."""
        learning = ThermalLearning()

        # Add some sample data
        learning.add_slope_sample("medium", 0.5, 0.3)
        learning.add_slope_sample("high", 0.8, 0.5)
        learning.add_response_event(12.5)

        # Serialize to dict
        data = learning.to_dict()

        # Verify structure
        assert isinstance(data, dict)
        assert "slope_samples" in data
        assert "response_events" in data
        assert "slope_count" in data
        assert "slope_mean" in data
        assert "slope_M2" in data
        assert "slope_max" in data

        # Verify data is preserved (at least partially for windowing)
        assert len(data["slope_samples"]) > 0
        assert len(data["response_events"]) > 0
        assert data["slope_count"] > 0

    def test_learning_from_dict_deserialization(self):
        """Test that learning data can be restored from dict."""
        # Create original learning instance with data
        original = ThermalLearning()
        original.add_slope_sample("medium", 0.5, 0.3)
        original.add_slope_sample("high", 0.8, 0.5)
        original.add_slope_sample("low", 0.3, 0.2)
        original.add_response_event(12.5)
        original.add_response_event(15.0)

        # Serialize
        data = original.to_dict()

        # Restore to new instance
        restored = ThermalLearning.from_dict(data)

        # Verify restoration
        assert restored.slope_count == original.slope_count
        assert restored.slope_mean == pytest.approx(original.slope_mean, rel=1e-5)
        assert restored.slope_max == pytest.approx(original.slope_max, rel=1e-5)
        assert len(restored.response_events) == len(original.response_events)

    def test_learning_empty_dict_initialization(self):
        """Test that learning can handle empty dict initialization."""
        restored = ThermalLearning.from_dict({})

        assert restored.slope_count == 0
        assert restored.slope_mean == 0.0
        assert restored.slope_max == 0.0
        assert len(restored.response_events) == 0
        assert not restored.is_ready()

    def test_learning_persistence_after_reset(self):
        """Test that reset clears all data including persistence data."""
        learning = ThermalLearning()

        # Add data
        learning.add_slope_sample("medium", 0.5, 0.3)
        learning.add_response_event(12.5)
        assert learning.slope_count > 0

        # Reset
        learning.reset()

        # Verify everything is cleared
        data = learning.to_dict()
        assert len(data["slope_samples"]) == 0
        assert len(data["response_events"]) == 0
        assert data["slope_count"] == 0
        assert data["slope_mean"] == 0.0
        assert data["slope_max"] == 0.0

    def test_learning_persistence_above_min_samples(self):
        """Test that all samples above 200 are persisted so 100% can be reached."""
        learning = ThermalLearning()

        # Add 240 samples (== _min_samples, needed for 100% progress)
        for _ in range(240):
            learning.add_slope_sample("medium", 0.5, 0.3)

        assert learning.slope_sample_count() == 240

        # Serialize and restore
        data = learning.to_dict()
        restored = ThermalLearning.from_dict(data)

        # All 240 samples must survive the round-trip
        assert len(data["slope_samples"]) == 240
        assert restored.slope_sample_count() == 240
        assert restored.get_progress() == 100.0
        assert restored.is_ready()

    def test_learning_sliding_window_cleanup(self):
        """Test that old samples are cleaned up from serialization."""
        learning = ThermalLearning()

        # Manually add old samples (simulate data from a week ago)
        DAYS_IN_SECONDS = 24 * 3600
        old_timestamp = time.time() - (8 * DAYS_IN_SECONDS)  # 8 days ago
        learning.slope_samples = [
            (old_timestamp, "medium", 0.5),
            (time.time(), "high", 0.8),
        ]
        learning.response_events = [
            (old_timestamp, 12.5),
            (time.time(), 15.0),
        ]

        # Serialize and restore (should clean up old data)
        data = learning.to_dict()
        restored = ThermalLearning.from_dict(data)

        # Old samples should be removed (only recent ones kept)
        assert len(restored.slope_samples) == 1
        assert len(restored.response_events) == 1

    @pytest.mark.asyncio
    async def test_integration_storage_persistence(self, hass: HomeAssistant):
        """Test that learning data is saved to and loaded from storage."""
        # Create a mock config entry
        entry = MagicMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_123"
        entry.data = {
            CONF_CLIMATE_ENTITY: "climate.test",
            CONF_DEADBAND: DEFAULT_DEADBAND,
        }
        entry.options = {}
        entry.async_on_unload = MagicMock()

        # Mock the storage with recent timestamps
        current_time = time.time()
        mock_store_data = {
            "slope_samples": [(current_time - 3600, "medium", 0.5)],  # 1 hour ago
            "response_events": [(current_time - 3600, 12.5)],  # 1 hour ago
            "slope_count": 5,
            "slope_mean": 0.6,
            "slope_M2": 0.1,
            "slope_max": 0.9,
        }

        with patch("custom_components.smart_fan_controller.Store") as mock_store_class:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value=mock_store_data)
            mock_store_instance.async_save = AsyncMock()
            mock_store_class.return_value = mock_store_instance

            # Mock other HA components
            with patch("custom_components.smart_fan_controller.async_track_time_interval"):
                with patch("custom_components.smart_fan_controller.async_track_state_change_event"):
                    with patch.object(hass.config_entries, "async_forward_entry_setups", return_value=True):
                        # Setup the integration
                        result = await async_setup_entry(hass, entry)

                        assert result is True

                        # Verify store was created with correct parameters
                        mock_store_class.assert_called_once()

                        # Verify data was loaded
                        mock_store_instance.async_load.assert_called_once()

                        # Verify controller has restored learning data
                        controller = hass.data[DOMAIN][entry.entry_id]["controller"]
                        # After window cleanup, data should still be there (timestamps are recent)
                        assert len(controller.learning.slope_samples) > 0
                        assert len(controller.learning.response_events) > 0
