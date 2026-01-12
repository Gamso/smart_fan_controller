"""
Adaptive thermal inertia calibration and threshold management.
"""
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional

_LOGGER = logging.getLogger(__name__)

@dataclass
class ThermalMetrics:
    """Store thermal system metrics."""
    timestamp: float
    temperature: float
    target_temperature: float
    hvac_mode: str
    fan_mode: str
    
    def to_dict(self):
        """Convert to dictionary for serialization."""
        return asdict(self)


@dataclass
class AdaptiveThresholds:
    """Store adaptive threshold values."""
    soft_error:  float
    hard_error: float
    projected_error_threshold: float
    thermal_time_constant: float  # τ in seconds
    confidence:  float  # 0.0 to 1.0
    
    def to_dict(self):
        """Convert to dictionary for serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict):
        """Create from dictionary."""
        return cls(**data)


class ThermalInertiaAdapter: 
    """
    Autonomously calibrate and adapt control thresholds based on system behavior.
    
    This adapter: 
    1. Measures thermal inertia (time constant τ)
    2. Tracks performance metrics (overshoot, settling time, oscillation)
    3. Dynamically adjusts soft_error, hard_error, projected_error_threshold
    4. Persists learning between restarts
    """
    
    def __init__(self, deadband: float, store_callback=None):
        """
        Initialize the adapter.
        
        Args:
            deadband: Temperature tolerance zone (°C)
            store_callback: Optional callback to persist state (hass. data storage)
        """
        self._deadband = deadband
        self._store_callback = store_callback
        
        # Learning window
        self._learning_window: list[ThermalMetrics] = []
        self._max_window_size = 1000  # Keep last ~33 hours at 2-min intervals
        
        # Performance tracking
        self._oscillation_count = 0  # How many fan changes in short period
        self._overshoot_max = 0.0  # Maximum overshoot observed
        self._settling_time = 0.0  # Time to settle within deadband (minutes)
        self._last_adaptation_time:  Optional[float] = None
        self._adaptation_interval = 30 * 60  # 30 minutes in seconds
        
        # Thermal model
        self._thermal_time_constant = 300.0  # τ (seconds) - initial estimate
        self._fan_change_markers: list[float] = []  # Track when fan speed changed
        
        # Current thresholds
        self._thresholds = AdaptiveThresholds(
            soft_error=deadband * 1.5,
            hard_error=deadband * 3.0,
            projected_error_threshold=deadband * 2.0,
            thermal_time_constant=self._thermal_time_constant,
            confidence=0.1  # Low confidence at start
        )
        
        # Historical thresholds for smoothing
        self._threshold_history: list[AdaptiveThresholds] = []
        
    def record_state(self, current_temp: float, target_temp: float, 
                     hvac_mode: str, fan_mode: str, current_time: float):
        """
        Record thermal state for analysis.
        
        Args:
            current_temp: Current temperature (°C)
            target_temp: Target temperature (°C)
            hvac_mode: 'heat' or 'cool'
            fan_mode: Current fan speed mode
            current_time: Unix timestamp
        """
        metric = ThermalMetrics(
            timestamp=current_time,
            temperature=current_temp,
            target_temperature=target_temp,
            hvac_mode=hvac_mode,
            fan_mode=fan_mode
        )
        
        self._learning_window.append(metric)
        
        # Keep window bounded
        if len(self._learning_window) > self._max_window_size:
            self._learning_window.pop(0)
        
        # Check if time to adapt
        if self._last_adaptation_time is None:
            self._last_adaptation_time = current_time
        elif current_time - self._last_adaptation_time >= self._adaptation_interval:
            self._adapt_thresholds(current_time)
            self._last_adaptation_time = current_time
    
    def mark_fan_change(self, current_time: float):
        """Mark when a fan speed change occurs for inertia analysis."""
        self._fan_change_markers.append(current_time)
        # Keep last 20 changes
        if len(self._fan_change_markers) > 20:
            self._fan_change_markers.pop(0)
    
    def _adapt_thresholds(self, current_time:  float):
        """
        Core adaptation logic - recalculate thresholds based on observed behavior.
        Called every 30 minutes.
        """
        if len(self._learning_window) < 15:  # Need ~30 min of data
            _LOGGER.debug("Not enough data yet for adaptation")
            return
        
        # 1. Measure thermal time constant
        tau = self._estimate_time_constant()
        
        # 2. Calculate performance metrics
        overshoot = self._calculate_overshoot()
        settling_time = self._calculate_settling_time()
        oscillation = self._calculate_oscillation()
        
        # 3. Compute confidence (0-1: how much we trust the measurements)
        confidence = min(1.0, self._thresholds.confidence + 0.1)
        
        # 4. Derive new thresholds
        new_soft_error = self._compute_soft_error(tau, overshoot, oscillation)
        new_hard_error = self._compute_hard_error(tau, overshoot)
        new_projected = self._compute_projected_threshold(tau, settling_time)
        
        # 5. Smooth transitions (weighted average with history)
        old_soft = self._thresholds.soft_error
        old_hard = self._thresholds.hard_error
        old_proj = self._thresholds.projected_error_threshold
        
        smooth_factor = 0.7  # 70% new, 30% old
        soft_error = (smooth_factor * new_soft_error) + ((1 - smooth_factor) * old_soft)
        hard_error = (smooth_factor * new_hard_error) + ((1 - smooth_factor) * old_hard)
        projected_error_threshold = (smooth_factor * new_projected) + ((1 - smooth_factor) * old_proj)
        
        # 6. Update thresholds
        old_thresholds = self._thresholds
        self._thresholds = AdaptiveThresholds(
            soft_error=soft_error,
            hard_error=hard_error,
            projected_error_threshold=projected_error_threshold,
            thermal_time_constant=tau,
            confidence=confidence
        )
        
        self._threshold_history.append(old_thresholds)
        if len(self._threshold_history) > 48:  # Keep 48 cycles = 24 hours
            self._threshold_history.pop(0)
        
        _LOGGER.info(
            "Adaptation cycle:  τ=%.0fs, overshoot=%.2f°C, settling=%.1fmin, "
            "oscillation=%d, confidence=%.1f%% → "
            "soft=%.2f°C, hard=%.2f°C, proj=%.2f°C",
            tau, overshoot, settling_time, oscillation, confidence * 100,
            soft_error, hard_error, projected_error_threshold
        )
        
        # 7. Persist if callback available
        if self._store_callback:
            self._store_callback({
                "thresholds": self._thresholds.to_dict(),
                "history": [t.to_dict() for t in self._threshold_history],
                "last_adaptation":  current_time
            })
    
    def _estimate_time_constant(self) -> float:
        """
        Estimate thermal time constant (τ) from recent temperature response.
        
        τ ≈ time for system to reach 63% of final value after step change.
        """
        if len(self._fan_change_markers) < 2:
            return self._thermal_time_constant
        
        # Look at last fan change
        last_change_time = self._fan_change_markers[-1]
        
        # Find corresponding data point
        change_idx = None
        for i, metric in enumerate(self._learning_window):
            if metric.timestamp >= last_change_time:
                change_idx = i
                break
        
        if change_idx is None or change_idx + 5 >= len(self._learning_window):
            return self._thermal_time_constant
        
        # Analyze temperature response after change
        temps_after = [self._learning_window[i].temperature 
                       for i in range(change_idx, min(change_idx + 15, len(self._learning_window)))]
        
        if len(temps_after) < 3:
            return self._thermal_time_constant
        
        # Simple exponential fit:  estimate τ from rate of change
        initial_temp = temps_after[0]
        final_temp = temps_after[-1]
        
        if abs(final_temp - initial_temp) < 0.01: 
            return self._thermal_time_constant
        
        # Time between measurements (2 minutes = 120 seconds)
        dt = 120  # seconds
        
        # Find when we reach ~63% of change
        target_delta = 0.63 * (final_temp - initial_temp)
        
        tau_estimate = self._thermal_time_constant
        for i in range(1, len(temps_after)):
            if abs(temps_after[i] - initial_temp) >= abs(target_delta):
                tau_estimate = i * dt  # Linear approximation
                break
        
        # Update with exponential smoothing
        self._thermal_time_constant = (0.8 * tau_estimate) + (0.2 * self._thermal_time_constant)
        
        return self._thermal_time_constant
    
    def _calculate_overshoot(self) -> float:
        """
        Calculate maximum overshoot:  how much temperature exceeds target.
        """
        overshoot = 0.0
        
        for metric in self._learning_window[-100:]:  # Last ~3.3 hours
            error = metric.temperature - metric.target_temperature
            
            # In cool mode, error direction reverses
            if metric.hvac_mode == "cool":
                error = -error
            
            # Only count positive errors (overshooting)
            if error > 0:
                overshoot = max(overshoot, error)
        
        self._overshoot_max = overshoot
        return overshoot
    
    def _calculate_settling_time(self) -> float:
        """
        Calculate time to settle within deadband.
        Returns minutes.
        """
        if len(self._learning_window) < 10:
            return 0.0
        
        # Look at recent data
        recent = self._learning_window[-50:]
        
        settling_time = 0.0
        for i, metric in enumerate(recent):
            error = abs(metric.temperature - metric.target_temperature)
            if error <= self._deadband:
                # Found settling point
                if i > 0:
                    start_time = recent[0].timestamp
                    settling_time = (metric.timestamp - start_time) / 60  # Convert to minutes
                break
        
        self._settling_time = settling_time
        return settling_time
    
    def _calculate_oscillation(self) -> int:
        """
        Count fan mode changes in last 30 minutes (oscillation indicator).
        """
        if len(self._fan_change_markers) == 0:
            self._oscillation_count = 0
            return 0
        
        # Count changes in last 30 minutes
        cutoff_time = self._learning_window[-1].timestamp - (30 * 60)
        recent_changes = sum(1 for t in self._fan_change_markers if t >= cutoff_time)
        
        self._oscillation_count = recent_changes
        return recent_changes
    
    def _compute_soft_error(self, tau: float, overshoot: float, oscillation: int) -> float:
        """
        Compute soft_error threshold. 
        
        Logic:
        - Base on deadband × factor
        - If overshoot high → increase soft_error to be more patient
        - If oscillating → increase to reduce unnecessary changes
        """
        base = self._deadband * 1.5
        
        # Penalty for overshoot
        overshoot_penalty = 0.0
        if overshoot > self._deadband:
            overshoot_penalty = (overshoot - self._deadband) * 0.3
        
        # Penalty for oscillation (too many changes)
        oscillation_penalty = 0.0
        if oscillation > 4:  # More than 4 changes in 30 min is oscillation
            oscillation_penalty = self._deadband * 0.5
        
        result = base + overshoot_penalty + oscillation_penalty
        
        # Clamp to reasonable range
        return max(self._deadband * 1.0, min(self._deadband * 3.0, result))
    
    def _compute_hard_error(self, tau: float, overshoot: float) -> float:
        """
        Compute hard_error (emergency) threshold.
        
        Logic:
        - Base on deadband × 3
        - If overshoot is extreme → reduce hard_error to trigger emergency sooner
        """
        base = self._deadband * 3.0
        
        # If system is overshooting significantly, need tighter emergency threshold
        if overshoot > self._deadband * 2:
            return self._deadband * 2.0
        
        return base
    
    def _compute_projected_threshold(self, tau: float, settling_time: float) -> float:
        """
        Compute projected_error_threshold for predictive boosting.
        
        Logic:
        - Base on deadband × 2
        - If settling time is long → increase threshold to be more proactive
        - If tau is short → decrease threshold (system responds quickly)
        """
        base = self._deadband * 2.0
        
        # Long settling time → need better prediction
        if settling_time > 30:  # More than 30 min to settle
            settling_factor = 1.5
        elif settling_time > 15:
            settling_factor = 1.2
        else:
            settling_factor = 1.0
        
        # Short tau → system is responsive, can use tighter thresholds
        if tau < 180:  # Less than 3 minutes
            tau_factor = 0.8
        elif tau < 300:  # Less than 5 minutes
            tau_factor = 0.9
        else:
            tau_factor = 1.0
        
        result = base * settling_factor * tau_factor
        
        return max(self._deadband * 1.0, min(self._deadband * 3.0, result))
    
    @property
    def soft_error(self) -> float:
        """Get current soft error threshold."""
        return self._thresholds.soft_error
    
    @property
    def hard_error(self) -> float:
        """Get current hard error threshold."""
        return self._thresholds.hard_error
    
    @property
    def projected_error_threshold(self) -> float:
        """Get current projected error threshold."""
        return self._thresholds.projected_error_threshold
    
    @property
    def thermal_time_constant(self) -> float:
        """Get estimated thermal time constant (τ in seconds)."""
        return self._thermal_time_constant
    
    @property
    def thresholds(self) -> AdaptiveThresholds:
        """Get current thresholds object."""
        return self._thresholds
    
    @property
    def confidence(self) -> float:
        """Get confidence level (0-1) in current adaptation."""
        return self._thresholds.confidence
    
    def restore_from_state(self, state: dict):
        """
        Restore learning state from persistent storage.
        
        Args:
            state: Dictionary with 'thresholds', 'history', 'last_adaptation'
        """
        if not state: 
            return
        
        try:
            if "thresholds" in state: 
                self._thresholds = AdaptiveThresholds.from_dict(state["thresholds"])
            
            if "history" in state:
                self._threshold_history = [
                    AdaptiveThresholds.from_dict(t) 
                    for t in state["history"]
                ]
            
            if "last_adaptation" in state:
                self._last_adaptation_time = state["last_adaptation"]
            
            _LOGGER.info(
                "Restored adaptive state:  soft=%.2f°C, hard=%.2f°C, "
                "proj=%.2f°C, τ=%.0fs, confidence=%.1f%%",
                self._thresholds.soft_error,
                self._thresholds.hard_error,
                self._thresholds.projected_error_threshold,
                self._thresholds.thermal_time_constant,
                self._thresholds.confidence * 100
            )
        except Exception as e:
            _LOGGER.warning("Failed to restore adaptive state: %s", e)
    
    def get_diagnostic_data(self) -> dict:
        """Return diagnostic data for debugging."""
        return {
            "thermal_time_constant": self._thermal_time_constant,
            "overshoot_max": self._overshoot_max,
            "settling_time_minutes": self._settling_time,
            "oscillation_count_30min": self._oscillation_count,
            "current_thresholds": self._thresholds.to_dict(),
            "confidence": self._thresholds.confidence,
            "learning_window_size": len(self._learning_window),
            "threshold_history_size": len(self._threshold_history)
        }
