"""Adaptive learning system for Smart Fan Controller."""
import logging
import time
from typing import Dict, Any, Optional, List

from .learning_storage import LearningStorage

_LOGGER = logging.getLogger(__name__)

# Learning thresholds and constants
CONFIDENCE_THRESHOLD_LOW = 0.3  # Below this: use static parameters only
CONFIDENCE_THRESHOLD_HIGH = 0.7  # Above this: primarily use learned parameters
MIN_ACTIVATIONS_FOR_RECOMMENDATION = 5  # Minimum fan mode activations before recommending
MIN_OBSERVATION_TIME_SECONDS = 120  # Minimum time between observations for learning
MIN_LEARNING_RATE = 0.05  # Minimum allowed learning rate
MAX_LEARNING_RATE = 0.2  # Maximum allowed learning rate
OVERSHOOT_TEMPERATURE_THRESHOLD = 0.2  # Temperature threshold for overshoot detection (°C)
MIN_TEMP_CHANGE_RATE_THRESHOLD = 0.01  # Minimum temp change rate to avoid division errors (°C/h)
MAX_THERMAL_INERTIA = 10.0  # Maximum reasonable thermal inertia value (safety bound)

class AdaptiveLearning:
    """
    Adaptive learning system that learns fan mode dynamics and thermal behavior.
    
    This class observes the system behavior and continuously learns:
    - Fan mode effectiveness (how each speed affects temperature)
    - Thermal inertia characteristics
    - Optimal control parameters
    """

    def __init__(
        self,
        storage: LearningStorage,
        enable_learning: bool = True,
        learning_rate: float = 0.1
    ):
        """
        Initialize adaptive learning system.
        
        Args:
            storage: Learning storage instance
            enable_learning: Whether learning is enabled
            learning_rate: Learning rate for parameter updates (0.05-0.2)
        """
        self._storage = storage
        self._enable_learning = enable_learning
        self._learning_rate = max(MIN_LEARNING_RATE, min(MAX_LEARNING_RATE, learning_rate))
        
        # Observation tracking
        self._last_observation_time: float = time.time()
        self._last_temp: Optional[float] = None
        self._last_slope: Optional[float] = None
        self._last_fan_mode: Optional[str] = None
        self._last_target_temp: Optional[float] = None
        self._fan_change_time: Optional[float] = None
        self._temp_at_fan_change: Optional[float] = None
        self._slope_at_fan_change: Optional[float] = None
        
        _LOGGER.info(
            "Adaptive learning initialized (enabled=%s, rate=%.2f, confidence=%.2f)",
            self._enable_learning,
            self._learning_rate,
            self._storage.get_learning_confidence()
        )
    
    def get_adaptive_parameters(
        self,
        static_deadband: float,
        static_soft_error: float,
        static_hard_error: float,
        static_min_interval: int,
        static_projected_error: float
    ) -> Dict[str, float]:
        """
        Calculate adaptive parameters based on learning confidence.
        
        Blends static and learned parameters based on confidence level:
        - Low confidence (<0.3): Use static parameters
        - Medium confidence (0.3-0.7): Blend static and learned
        - High confidence (>0.7): Use learned parameters
        
        Returns:
            Dictionary with adaptive parameter values
        """
        if not self._enable_learning:
            return {
                "deadband": static_deadband,
                "soft_error": static_soft_error,
                "hard_error": static_hard_error,
                "min_interval": static_min_interval,
                "projected_error_threshold": static_projected_error
            }
        
        confidence = self._storage.get_learning_confidence()
        thermal_params = self._storage.get_thermal_parameters()
        
        # Calculate learned parameters
        learned_inertia = thermal_params["learned_thermal_inertia"]
        learned_response_time = thermal_params["avg_response_time"] / 60.0  # Convert to minutes
        
        # Calculate adaptive thresholds based on thermal inertia
        learned_soft_error = learned_inertia * 0.6
        learned_hard_error = learned_inertia * 1.2
        learned_deadband = learned_inertia * 0.4
        learned_projected_error = learned_inertia * 1.0
        learned_min_interval = max(5, min(20, learned_response_time * 1.5))
        
        # Blend based on confidence
        if confidence < CONFIDENCE_THRESHOLD_LOW:
            # Low confidence: use static parameters
            blend_factor = 0.0
        elif confidence < CONFIDENCE_THRESHOLD_HIGH:
            # Medium confidence: blend proportionally
            blend_factor = (confidence - CONFIDENCE_THRESHOLD_LOW) / (CONFIDENCE_THRESHOLD_HIGH - CONFIDENCE_THRESHOLD_LOW)
        else:
            # High confidence: use learned parameters
            blend_factor = 1.0
        
        return {
            "deadband": self._blend_values(static_deadband, learned_deadband, blend_factor),
            "soft_error": self._blend_values(static_soft_error, learned_soft_error, blend_factor),
            "hard_error": self._blend_values(static_hard_error, learned_hard_error, blend_factor),
            "min_interval": self._blend_values(static_min_interval, learned_min_interval, blend_factor),
            "projected_error_threshold": self._blend_values(
                static_projected_error, learned_projected_error, blend_factor
            )
        }
    
    def _blend_values(self, static_val: float, learned_val: float, blend_factor: float) -> float:
        """Blend static and learned values based on blend factor."""
        return static_val * (1 - blend_factor) + learned_val * blend_factor
    
    def observe_decision(
        self,
        current_temp: float,
        target_temp: float,
        current_slope: float,
        current_fan: str,
        new_fan: str,
        hvac_mode: str,
        decision_reason: str
    ) -> None:
        """
        Observe a controller decision and learn from it.
        
        Args:
            current_temp: Current temperature
            target_temp: Target temperature
            current_slope: Current thermal slope
            current_fan: Current fan mode before decision
            new_fan: New fan mode after decision
            hvac_mode: HVAC mode (heat/cool)
            decision_reason: Reason for the decision
        """
        if not self._enable_learning:
            return
        
        current_time = time.time()
        
        # Learn from previous fan mode if we have enough data
        if (self._last_temp is not None and 
            self._last_slope is not None and 
            self._last_fan_mode is not None):
            
            time_elapsed = current_time - self._last_observation_time
            
            # Only learn if enough time has passed (at least 2 minutes)
            if time_elapsed >= MIN_OBSERVATION_TIME_SECONDS:
                self._learn_from_observation(
                    fan_mode=self._last_fan_mode,
                    temp_before=self._last_temp,
                    temp_after=current_temp,
                    slope_before=self._last_slope,
                    slope_after=current_slope,
                    time_elapsed=time_elapsed,
                    target_temp=target_temp,
                    hvac_mode=hvac_mode
                )
        
        # If fan mode changed, track it for future learning
        if current_fan != new_fan:
            self._fan_change_time = current_time
            self._temp_at_fan_change = current_temp
            self._slope_at_fan_change = current_slope
            
            _LOGGER.debug(
                "Fan mode change detected: %s -> %s (reason: %s)",
                current_fan, new_fan, decision_reason
            )
        
        # Update tracking variables
        self._last_observation_time = current_time
        self._last_temp = current_temp
        self._last_slope = current_slope
        self._last_fan_mode = new_fan
        self._last_target_temp = target_temp
        
        # Update decision count
        self._storage.update_metadata(decision_made=True)
    
    def _learn_from_observation(
        self,
        fan_mode: str,
        temp_before: float,
        temp_after: float,
        slope_before: float,
        slope_after: float,
        time_elapsed: float,
        target_temp: float,
        hvac_mode: str
    ) -> None:
        """
        Learn from an observation period.
        
        Args:
            fan_mode: Fan mode during observation
            temp_before: Temperature at start
            temp_after: Temperature at end
            slope_before: Slope at start
            slope_after: Slope at end
            time_elapsed: Time elapsed in seconds
            target_temp: Target temperature
            hvac_mode: HVAC mode (heat/cool)
        """
        # Calculate observed changes
        temp_change = temp_after - temp_before
        slope_change = slope_after - slope_before
        temp_change_rate = temp_change / (time_elapsed / 3600.0)  # °C per hour
        
        # Calculate error metrics
        error_before = abs(temp_before - target_temp)
        error_after = abs(temp_after - target_temp)
        error_improvement = error_before - error_after
        
        # Determine if this was a successful control action
        moving_toward_target = error_improvement > 0
        
        # Check for overshoot/undershoot
        overshoot = False
        undershoot = False
        
        if hvac_mode == "heat":
            overshoot = temp_after > target_temp + OVERSHOOT_TEMPERATURE_THRESHOLD
            undershoot = temp_after < temp_before and temp_before < target_temp
        else:  # cool
            overshoot = temp_after < target_temp - OVERSHOOT_TEMPERATURE_THRESHOLD
            undershoot = temp_after > temp_before and temp_before > target_temp
        
        # Calculate effectiveness score (0-1)
        # High score if moving toward target without overshoot
        if moving_toward_target and not overshoot:
            effectiveness = min(1.0, 0.5 + error_improvement / 0.5)
        elif moving_toward_target:
            effectiveness = 0.5
        else:
            effectiveness = max(0.0, 0.5 - abs(error_improvement) / 0.5)
        
        # Update fan mode profile
        self._storage.update_fan_mode_profile(
            fan_mode=fan_mode,
            slope_change=slope_change,
            temp_change_rate=temp_change_rate,
            effectiveness_score=effectiveness,
            overshoot=overshoot,
            undershoot=undershoot
        )
        
        # Update thermal inertia estimate
        # Thermal inertia = resistance to temperature change
        # Higher inertia = slower temperature response
        if abs(temp_change_rate) > MIN_TEMP_CHANGE_RATE_THRESHOLD:
            observed_inertia = abs(error_before) / abs(temp_change_rate)
            # Bound the inertia value to reasonable range
            observed_inertia = min(observed_inertia, MAX_THERMAL_INERTIA)
            self._storage.update_thermal_parameters(thermal_inertia=observed_inertia)
        
        # Track prediction success
        if moving_toward_target:
            self._storage.update_metadata(prediction_successful=True)
        
        _LOGGER.debug(
            "Learning update: fan_mode=%s, temp_change=%.2f, slope_change=%.2f, "
            "effectiveness=%.2f, overshoot=%s",
            fan_mode, temp_change, slope_change, effectiveness, overshoot
        )
    
    def get_fan_mode_effectiveness(self, fan_mode: str) -> float:
        """
        Get effectiveness score for a fan mode.
        
        Returns:
            Effectiveness score (0-1), higher is better
        """
        profile = self._storage.get_fan_mode_profile(fan_mode)
        return profile["effectiveness_score"]
    
    def get_recommended_fan_mode(
        self,
        available_modes: List[str],
        current_error: float,
        current_slope: float,
        hvac_mode: str
    ) -> Optional[str]:
        """
        Recommend a fan mode based on learned effectiveness.
        
        Only provides recommendations when confidence is high (>0.7).
        
        Args:
            available_modes: List of available fan modes
            current_error: Current temperature error
            current_slope: Current thermal slope
            hvac_mode: HVAC mode (heat/cool)
        
        Returns:
            Recommended fan mode or None if confidence is too low
        """
        if not self._enable_learning:
            return None
        
        confidence = self._storage.get_learning_confidence()
        if confidence < CONFIDENCE_THRESHOLD_HIGH:
            return None
        
        # Get profiles for all modes
        profiles = {}
        for mode in available_modes:
            profile = self._storage.get_fan_mode_profile(mode)
            if profile["activation_count"] < MIN_ACTIVATIONS_FOR_RECOMMENDATION:
                # Not enough data for this mode
                continue
            profiles[mode] = profile
        
        if not profiles:
            return None
        
        # Score each mode based on current situation
        scores = {}
        for mode, profile in profiles.items():
            # Base score from effectiveness
            score = profile["effectiveness_score"]
            
            # Adjust based on error magnitude
            # Higher error needs more aggressive modes
            if abs(current_error) > 0.5:
                # Prefer modes with stronger response
                score += abs(profile["avg_slope_change"]) * 0.2
            
            # Adjust for overshoot history
            if profile["activation_count"] > 0:
                overshoot_rate = profile["total_overshoot_events"] / profile["activation_count"]
                score -= overshoot_rate * 0.3
            
            scores[mode] = score
        
        # Return mode with highest score
        best_mode = max(scores, key=scores.get)
        
        _LOGGER.debug(
            "Learned recommendation: %s (score=%.2f, confidence=%.2f)",
            best_mode, scores[best_mode], confidence
        )
        
        return best_mode
    
    def get_learning_info(self) -> Dict[str, Any]:
        """
        Get information about current learning state.
        
        Returns:
            Dictionary with learning metrics and status
        """
        state = self._storage.get_state()
        metadata = state["learning_metadata"]
        thermal_params = state["thermal_parameters"]
        
        fan_modes_info = {}
        for mode, profile in state["fan_mode_profiles"].items():
            fan_modes_info[mode] = {
                "activations": profile["activation_count"],
                "effectiveness": round(profile["effectiveness_score"], 2),
                "avg_slope_change": round(profile["avg_slope_change"], 3),
                "overshoots": profile["total_overshoot_events"],
                "undershoots": profile["total_undershoot_events"]
            }
        
        return {
            "enabled": self._enable_learning,
            "confidence": round(metadata["learning_confidence"], 2),
            "total_decisions": metadata["total_decisions"],
            "successful_predictions": metadata["successful_predictions"],
            "thermal_inertia": round(thermal_params["learned_thermal_inertia"], 3),
            "avg_response_time": round(thermal_params["avg_response_time"], 1),
            "fan_modes": fan_modes_info,
            "last_saved": metadata["last_saved"]
        }
