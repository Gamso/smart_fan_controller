"""Persistent learning storage for Smart Fan Controller."""
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional

from .const import DEFAULT_PROFILE_LEARNING_RATE, DEFAULT_THERMAL_LEARNING_RATE

_LOGGER = logging.getLogger(__name__)

class LearningStorage:
    """Manages persistent storage of learning data."""

    def __init__(self, storage_path: str, climate_entity_id: str):
        """
        Initialize learning storage.
        
        Args:
            storage_path: Directory path for storing learning data
            climate_entity_id: ID of the climate entity (used for filename)
        """
        self._storage_path = storage_path
        self._climate_entity_id = climate_entity_id
        self._storage_file = self._get_storage_filename()
        self._learning_state: Dict[str, Any] = self._create_default_state()
        
    def _get_storage_filename(self) -> str:
        """Generate storage filename based on climate entity ID."""
        # Sanitize entity ID for filename
        safe_id = self._climate_entity_id.replace(".", "_").replace(":", "_")
        return os.path.join(self._storage_path, f"smart_fan_learning_{safe_id}.json")
    
    def _create_default_state(self) -> Dict[str, Any]:
        """Create default learning state structure."""
        return {
            "version": "1.0",
            "climate_entity_id": self._climate_entity_id,
            "fan_mode_profiles": {},
            "thermal_parameters": {
                "learned_thermal_inertia": 0.5,  # Default: medium inertia
                "optimal_prediction_window": 10.0,  # minutes
                "adaptive_soft_error": None,  # Will use static default initially
                "adaptive_hard_error": None,
                "adaptive_deadband": None,
                "avg_response_time": 300.0,  # seconds
            },
            "environment_metrics": {
                "avg_ambient_change_rate": 0.0,
                "typical_load_duration": 0.0,
            },
            "learning_metadata": {
                "total_decisions": 0,
                "successful_predictions": 0,
                "learning_confidence": 0.0,
                "created_at": datetime.now().isoformat(),
                "last_saved": None,
            }
        }
    
    def initialize_fan_mode_profile(self, fan_mode: str) -> None:
        """Initialize learning profile for a specific fan mode."""
        if fan_mode not in self._learning_state["fan_mode_profiles"]:
            self._learning_state["fan_mode_profiles"][fan_mode] = {
                "avg_slope_change": 0.0,
                "avg_response_time": 0.0,
                "effectiveness_score": 0.5,  # Neutral initial score
                "activation_count": 0,
                "total_overshoot_events": 0,
                "total_undershoot_events": 0,
                "avg_temp_change_rate": 0.0,
                "last_updated": None,
            }
            _LOGGER.info("Initialized learning profile for fan mode: %s", fan_mode)
    
    def load(self) -> bool:
        """
        Load learning state from disk.
        
        Returns:
            True if successfully loaded, False if file doesn't exist or is corrupt
        """
        if not os.path.exists(self._storage_file):
            _LOGGER.info("No existing learning state found at %s", self._storage_file)
            return False
        
        try:
            with open(self._storage_file, 'r', encoding='utf-8') as f:
                loaded_state = json.load(f)
            
            # Validate loaded state
            if not self._validate_state(loaded_state):
                _LOGGER.warning("Loaded learning state is invalid, using defaults")
                return False
            
            # Merge with default state to handle version upgrades
            self._learning_state = self._merge_with_defaults(loaded_state)
            
            _LOGGER.info(
                "Successfully loaded learning state with %d decisions and %.2f confidence",
                self._learning_state["learning_metadata"]["total_decisions"],
                self._learning_state["learning_metadata"]["learning_confidence"]
            )
            return True
            
        except (json.JSONDecodeError, IOError) as e:
            _LOGGER.error("Failed to load learning state: %s", e)
            return False
    
    def save(self) -> bool:
        """
        Save learning state to disk.
        
        Returns:
            True if successfully saved, False otherwise
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self._storage_file), exist_ok=True)
            
            # Update last saved timestamp
            self._learning_state["learning_metadata"]["last_saved"] = datetime.now().isoformat()
            
            # Write to temporary file first, then rename for atomic operation
            temp_file = self._storage_file + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self._learning_state, f, indent=2, ensure_ascii=False)
            
            # Atomic rename
            os.replace(temp_file, self._storage_file)
            
            _LOGGER.debug("Learning state saved successfully to %s", self._storage_file)
            return True
            
        except (IOError, OSError) as e:
            _LOGGER.error("Failed to save learning state: %s", e)
            return False
    
    def _validate_state(self, state: Dict[str, Any]) -> bool:
        """Validate loaded state has required structure."""
        required_keys = ["version", "fan_mode_profiles", "thermal_parameters", "learning_metadata"]
        return all(key in state for key in required_keys)
    
    def _merge_with_defaults(self, loaded_state: Dict[str, Any]) -> Dict[str, Any]:
        """Merge loaded state with defaults for backward compatibility."""
        default = self._create_default_state()
        
        # Deep merge strategy: keep loaded values, add missing defaults
        for key, default_value in default.items():
            if key not in loaded_state:
                loaded_state[key] = default_value
            elif isinstance(default_value, dict):
                # Recursively merge nested dictionaries
                for sub_key, sub_default in default_value.items():
                    if sub_key not in loaded_state[key]:
                        loaded_state[key][sub_key] = sub_default
        
        return loaded_state
    
    def get_state(self) -> Dict[str, Any]:
        """Get current learning state."""
        return self._learning_state
    
    def update_fan_mode_profile(
        self,
        fan_mode: str,
        slope_change: Optional[float] = None,
        response_time: Optional[float] = None,
        effectiveness_score: Optional[float] = None,
        temp_change_rate: Optional[float] = None,
        overshoot: bool = False,
        undershoot: bool = False
    ) -> None:
        """
        Update fan mode profile with new observations.
        
        Uses exponential moving average for smooth updates.
        
        Args:
            fan_mode: Fan mode being updated
            slope_change: Observed slope change after activation
            response_time: Time taken to reach target
            effectiveness_score: Performance score (0-1)
            temp_change_rate: Rate of temperature change
            overshoot: Whether overshoot occurred
            undershoot: Whether undershoot occurred
        """
        self.initialize_fan_mode_profile(fan_mode)
        
        profile = self._learning_state["fan_mode_profiles"][fan_mode]
        alpha = DEFAULT_PROFILE_LEARNING_RATE  # Exponential moving average weight for new observations
        
        if slope_change is not None:
            profile["avg_slope_change"] = (
                alpha * slope_change + (1 - alpha) * profile["avg_slope_change"]
            )
        
        if response_time is not None:
            profile["avg_response_time"] = (
                alpha * response_time + (1 - alpha) * profile["avg_response_time"]
            )
        
        if effectiveness_score is not None:
            profile["effectiveness_score"] = (
                alpha * effectiveness_score + (1 - alpha) * profile["effectiveness_score"]
            )
        
        if temp_change_rate is not None:
            profile["avg_temp_change_rate"] = (
                alpha * temp_change_rate + (1 - alpha) * profile["avg_temp_change_rate"]
            )
        
        if overshoot:
            profile["total_overshoot_events"] += 1
        
        if undershoot:
            profile["total_undershoot_events"] += 1
        
        profile["activation_count"] += 1
        profile["last_updated"] = datetime.now().isoformat()
        
        _LOGGER.debug(
            "Updated fan mode '%s' profile: activations=%d, avg_slope_change=%.3f",
            fan_mode, profile["activation_count"], profile["avg_slope_change"]
        )
    
    def update_thermal_parameters(
        self,
        thermal_inertia: Optional[float] = None,
        prediction_window: Optional[float] = None,
        response_time: Optional[float] = None
    ) -> None:
        """Update thermal system parameters."""
        params = self._learning_state["thermal_parameters"]
        alpha = DEFAULT_THERMAL_LEARNING_RATE  # Slower learning rate for global parameters
        
        if thermal_inertia is not None:
            params["learned_thermal_inertia"] = (
                alpha * thermal_inertia + (1 - alpha) * params["learned_thermal_inertia"]
            )
        
        if prediction_window is not None:
            params["optimal_prediction_window"] = (
                alpha * prediction_window + (1 - alpha) * params["optimal_prediction_window"]
            )
        
        if response_time is not None:
            params["avg_response_time"] = (
                alpha * response_time + (1 - alpha) * params["avg_response_time"]
            )
    
    def update_metadata(
        self,
        decision_made: bool = False,
        prediction_successful: Optional[bool] = None
    ) -> None:
        """Update learning metadata."""
        metadata = self._learning_state["learning_metadata"]
        
        if decision_made:
            metadata["total_decisions"] += 1
        
        if prediction_successful is not None and prediction_successful:
            metadata["successful_predictions"] += 1
        
        # Update confidence score
        if metadata["total_decisions"] > 0:
            min_observations = 100
            observation_confidence = min(1.0, metadata["total_decisions"] / min_observations)
            accuracy_confidence = metadata["successful_predictions"] / metadata["total_decisions"]
            
            metadata["learning_confidence"] = (
                observation_confidence * 0.5 + accuracy_confidence * 0.5
            )
    
    def get_fan_mode_profile(self, fan_mode: str) -> Dict[str, Any]:
        """Get learning profile for a specific fan mode."""
        self.initialize_fan_mode_profile(fan_mode)
        return self._learning_state["fan_mode_profiles"][fan_mode]
    
    def get_thermal_parameters(self) -> Dict[str, Any]:
        """Get thermal parameters."""
        return self._learning_state["thermal_parameters"]
    
    def get_learning_confidence(self) -> float:
        """Get current learning confidence level (0-1)."""
        return self._learning_state["learning_metadata"]["learning_confidence"]
    
    def reset_learning(self, full_reset: bool = True, fan_mode: Optional[str] = None) -> None:
        """
        Reset learning data.
        
        Args:
            full_reset: If True, reset all learning. If False, only reset specific fan_mode
            fan_mode: Specific fan mode to reset (only used if full_reset is False)
        """
        if full_reset:
            self._learning_state = self._create_default_state()
            _LOGGER.info("Performed full learning reset")
        elif fan_mode:
            if fan_mode in self._learning_state["fan_mode_profiles"]:
                del self._learning_state["fan_mode_profiles"][fan_mode]
                _LOGGER.info("Reset learning for fan mode: %s", fan_mode)
        
        self.save()
