import logging
import time
import statistics

from .const import (
    DELTA_TIME_CONTROL_LOOP
)

_LOGGER = logging.getLogger(__name__)


class ThermalLearning:
    """Auto-calibration of thermal parameters based on observed system behavior."""

    def __init__(self):
        # Data collection with sliding window
        self._slope_samples = []  # (timestamp, fan_mode, slope)
        self._response_events = []  # (timestamp, fan_change, time_to_slope_change)
        self._learning_window_hours = 168  # 7 days sliding window
        self._min_samples = 240  # Minimum samples for initial readiness (48-72h typical activity)
        self._ready_once = False  # Flag to track if we've ever reached ready state

        # Incremental statistics using Welford's algorithm
        self._slope_count = 0  # Number of slope samples processed
        self._slope_mean = 0.0  # Running mean of absolute slopes
        self._slope_m2 = 0.0  # Sum of squared differences for variance
        self._slope_max = 0.0  # Maximum absolute slope

    def reset(self) -> None:
        """Reset all learning data and statistics."""
        self._slope_samples.clear()
        self._response_events.clear()
        self._slope_count = 0
        self._slope_mean = 0.0
        self._slope_m2 = 0.0
        self._slope_max = 0.0
        self._ready_once = False
        _LOGGER.info("Learning: reset requested; data cleared")

    def add_slope_sample(self, fan_mode: str, slope: float, temperature_error: float = 0):
        """Record slope only if in normal operating range (not setpoint changes)."""
        # Ignore data during setpoint drop/night mode (error < -1°C)
        # Accept positive errors and small negative errors (normal operation)
        if temperature_error < -1.0:
            _LOGGER.debug("Learning: Skipped sample (setpoint drop, err=%.2f)", temperature_error)
            return  # Skip: Setpoint change, nuit, or emergency conditions

        # Ignore stagnation (no useful data)
        if abs(slope) < 0.15:
            _LOGGER.debug("Learning: Skipped sample (stagnation, slope=%.2f)", slope)
            return

        # Only collect meaningful transitions
        self._slope_samples.append((time.time(), fan_mode, slope))
        _LOGGER.debug("Learning: Collected slope sample #%d (fan=%s, slope=%.2f, err=%.2f)", len(self._slope_samples), fan_mode, slope, temperature_error)

        # Update incremental statistics using Welford's algorithm
        self._update_slope_stats(abs(slope))

        # Cleanup: keep only data within sliding window (7 days)
        cutoff_time = time.time() - (self._learning_window_hours * 3600)
        before = len(self._slope_samples)
        self._slope_samples = [(ts, mode, sl) for ts, mode, sl in self._slope_samples if ts > cutoff_time]

        # If some points were dropped → rebuild stats
        if len(self._slope_samples) < before:
            self.recompute_slope_stats()

    def _update_slope_stats(self, abs_slope: float) -> None:
        """Update slope statistics incrementally using Welford's algorithm."""
        self._slope_count += 1
        delta = abs_slope - self._slope_mean
        self._slope_mean += delta / self._slope_count
        delta2 = abs_slope - self._slope_mean
        self._slope_m2 += delta * delta2
        self._slope_max = max(self._slope_max, abs_slope)
        _LOGGER.debug("Learning: Updated stats (count=%d, mean=%.3f, max=%.3f)", self._slope_count, self._slope_mean, self._slope_max)

    def add_response_event(self, minutes_to_response: float):
        """Record time until slope changed significantly after fan change."""
        self._response_events.append((time.time(), minutes_to_response))
        _LOGGER.debug("Learning: Recorded response time #%d: %.1f min", len(self._response_events), minutes_to_response)

        # Cleanup: keep only data within sliding window (7 days)
        cutoff_time = time.time() - (self._learning_window_hours * 3600)
        self._response_events = [(ts, t) for ts, t in self._response_events if ts > cutoff_time]

    def slope_sample_count(self) -> int:
        """Return number of collected slope samples."""
        return len(self._slope_samples)

    def response_event_count(self) -> int:
        """Return number of recorded response events."""
        return len(self._response_events)

    def get_progress(self) -> float:
        """Return learning progress as percentage (0-100)."""
        sample_count = len(self._slope_samples)
        # Progress based on samples collected (no time limit after initial readiness)
        progress = min(100, (sample_count / self._min_samples) * 100)
        return progress

    def is_ready(self) -> bool:
        """Check if enough data has been collected."""
        if not self._ready_once and len(self._slope_samples) >= self._min_samples:
            self._ready_once = True
            _LOGGER.info("Learning: Initial readiness reached with %d samples", self._min_samples)
        return self._ready_once

    def compute_optimal_parameters(self) -> dict:
        """Calculate optimal parameters from learned data."""
        if not self.is_ready():
            return {}

        # Use incremental statistics (already computed on each sample)
        if self._slope_count == 0:
            return {}

        # Variance from Welford's algorithm
        slope_variance = self._slope_m2 / (self._slope_count - 1) if self._slope_count > 1 else 0.01
        slope_stdev = slope_variance**0.5  # Standard deviation

        # Analyze response times
        response_times = [t for _, t in self._response_events if t > 0]
        avg_response = statistics.mean(response_times) if response_times else 10.0

        # Compute optimal_limit_timeout based on response time
        # Inertia-aware: ~1.5x observed response time (≈ 1–2 time constants),
        # clamped to a practical range to avoid over-inertia or oscillations.
        optimal_limit_timeout = max(8, min(25, int(avg_response * 1.5)))

        # Adapt thresholds to slope characteristics
        # High volatility → larger deadbands to avoid oscillations
        volatility_factor = min(slope_stdev / max(self._slope_mean, 0.1), 3.0)

        optimal_deadband = 0.15 + (volatility_factor * 0.2)
        optimal_soft_error = 0.25 + (volatility_factor * 0.3)
        optimal_hard_error = 0.5 + (volatility_factor * 0.4)

        _LOGGER.info(
            "Auto-calibration complete: avg_slope=%.2f std=%.2f max=%.2f | avg_response=%.1fmin | limit_timeout=%d",
            self._slope_mean,
            slope_stdev,
            self._slope_max,
            avg_response,
            optimal_limit_timeout,
        )

        return {
            "deadband": round(optimal_deadband, 2),
            "soft_error": round(optimal_soft_error, 2),
            "hard_error": round(optimal_hard_error, 2),
            "limit_timeout": optimal_limit_timeout,
            "samples_count": self._slope_count,
            "response_samples": len(response_times),
        }

    def to_dict(self) -> dict:
        """Serialize for storage."""
        return {
            "slope_samples": self._slope_samples[-200:],
            "response_events": self._response_events[-100:],
            "slope_count": self._slope_count,
            "slope_mean": self._slope_mean,
            "slope_M2": self._slope_m2,
            "slope_max": self._slope_max,
        }

    def recompute_slope_stats(self) -> None:
        """Rebuild Welford statistics from current sliding window."""
        self._slope_count = 0
        self._slope_mean = 0.0
        self._slope_m2 = 0.0
        self._slope_max = 0.0

        for _, _, sl in self._slope_samples:
            self._update_slope_stats(abs(sl))

        _LOGGER.debug(
            "Learning: Recomputed stats from window (count=%d, mean=%.3f, max=%.3f)",
            self._slope_count,
            self._slope_mean,
            self._slope_max,
        )

    @classmethod
    def from_dict(cls, data: dict):
        """Restore from storage."""
        instance = cls()
        # Restore data and clean up old entries outside window
        instance._slope_samples = data.get("slope_samples", [])
        instance._response_events = data.get("response_events", [])

        # Restore incremental statistics
        instance._slope_count = data.get("slope_count", 0)
        instance._slope_mean = data.get("slope_mean", 0.0)
        instance._slope_m2 = data.get("slope_M2", 0.0)
        instance._slope_max = data.get("slope_max", 0.0)

        # Apply sliding window cleanup on restore
        cutoff_time = time.time() - (instance._learning_window_hours * 3600)
        instance._slope_samples = [(ts, mode, sl) for ts, mode, sl in instance._slope_samples if ts > cutoff_time]
        instance._response_events = [(ts, t) for ts, t in instance._response_events if ts > cutoff_time]

        # Rebuild stats from cleaned window
        instance.recompute_slope_stats()

        # Mark as ready once if we have enough data
        if instance._slope_count >= instance._min_samples:
            instance._ready_once = True

        return instance


class SmartFanController:
    """Decision engine for selecting fan mode based on thermal signals."""

    def __init__(
        self,
        fan_modes: list | None,
        deadband: float,
        min_interval: int,
        soft_error: float,
        hard_error: float,
        limit_timeout: int = 15,
        learning_data: dict | None = None,
    ):
        # Initialize the attribute even if it is None initially
        self._fan_modes: list | None = fan_modes

        # Config Flow values
        self._deadband = deadband
        self._min_interval = min_interval
        self._soft_error = soft_error
        self._hard_error = hard_error
        self._limit_timeout = limit_timeout
        self._threshold_slope = 0.1
        self._threshold_target_drop = -1.0

        # State variables
        self._previous_slope: float | None = None
        self._thermal_acceleration: float = 0.0
        self._slope_at_last_change: float = 0.0
        self._now: float = time.time()
        self._last_change_time: float = self._now - (self._limit_timeout * 60)
        self._last_slope_significant_change: float = self._now

        # Learning system
        if learning_data:
            self.learning = ThermalLearning.from_dict(learning_data)
        else:
            self.learning = ThermalLearning()

    @property
    def fan_modes(self) -> list | None:
        """Return available fan modes, if initialized."""
        return self._fan_modes

    @fan_modes.setter
    def fan_modes(self, modes: list | None) -> None:
        """Set available fan modes."""
        self._fan_modes = modes

    @property
    def _projected_error_threshold(self) -> float:
        """Calculate projected error threshold as midpoint between soft and hard error."""
        return (self._soft_error + self._hard_error) / 2

    def compute_temperature_projection(self, current_temp: float, vtherm_slope: float) -> float:
        """Estimate temperature projection in 10 min"""

        dt_hours = DELTA_TIME_CONTROL_LOOP / 60 # h
        d_slope = vtherm_slope - self._previous_slope if self._previous_slope is not None else 0.0 # °C/h
        a_inst = d_slope / dt_hours  # °C/h²

        # Low-pass filter on acceleration
        # Using 0.5/0.5 EMA to balance reactivity (VTherm already provides ~15-30min smoothing)
        # This gives ~8 min integration window vs 20 min with 0.3/0.7
        self._thermal_acceleration = (0.5 * a_inst) + (0.5 * self._thermal_acceleration) # °C/h

        # Parabolic forecast (t = 10 min)
        window_time = 10 /60 # h
        temp_proj = current_temp + (vtherm_slope * window_time) + (0.5 * self._thermal_acceleration * (window_time**2))
        return temp_proj

    def apply_deceleration_limit(self, current_index: int, new_index: int) -> int:
        """
        Ensure the fan speed decreases by no more than one step at a time
        to maintain system stability.
        """
        if (new_index - current_index) < -1:
            return current_index - 1
        return new_index

    def determine_final_index(self, current_index: int, new_index: int, minutes_since_change: float, force: bool) -> int:
        """Limit fan speed changes with safety guards."""
        if force:
            return self.apply_deceleration_limit(current_index, new_index)

        # Enforce the minimum time between two changes
        if minutes_since_change < self._min_interval:
            return current_index

        return self.apply_deceleration_limit(current_index, new_index)

    def update_new_fan_state(self, new_fan: int) -> dict:
        """Persist last change timestamp and return manual override payload."""
        self._last_change_time = time.time()

        return {
            "fan_mode": new_fan,
            "minutes_since_last_change": 0.0,
            "reason": "Manual Override"
        }

    def save_states(self, target_fan: str, current_fan: str | None, vtherm_slope: float, effective_slope: float, slope_change: bool):
        """Update states."""
        if target_fan != current_fan:
            self._last_change_time = self._now
            self._slope_at_last_change = effective_slope
            # Record response time for learning
            if self._last_slope_significant_change > 0:
                response_time = (self._now - self._last_slope_significant_change) / 60
                self.learning.add_response_event(response_time)

        if target_fan != current_fan or slope_change:
            self._previous_slope = vtherm_slope

        # Track significant slope changes for response time calculation
        if slope_change:
            self._last_slope_significant_change = self._now

    def calculate_decision(self, current_temp: float, target_temp: float, vtherm_slope: float, hvac_mode: str, current_fan: str | None) -> dict:
        """Compute new fan speed."""
        self._now = time.time()

        # Init slope states
        if self._previous_slope is None:
            self._previous_slope = vtherm_slope
            self._slope_at_last_change = vtherm_slope

        if not self._fan_modes:
            _LOGGER.warning("Fan modes are not initialized; holding current mode %s", current_fan)
            return {
                "fan_mode": current_fan,
                "reason": "No fan modes defined"
            }

        # Time since last fan change
        minutes_since_change = (self._now - self._last_change_time) / 60

        # -----------------------#
        # --- Error analysis ---#
        # -----------------------#
        # Effective slope: positive if moving towards target
        effective_slope = -vtherm_slope if hvac_mode == 'cool' else vtherm_slope
        projected_temperature = self.compute_temperature_projection(current_temp, vtherm_slope)
        # Current error (positive = need more heat/cool)
        current_temperature_error = (current_temp - target_temp) if hvac_mode == 'cool' else (target_temp - current_temp)
        # Projected error in 10 min (positive = will miss target)
        projected_temperature_error = (projected_temperature - target_temp) if hvac_mode == 'cool' else (target_temp - projected_temperature)

        # -------------------------#
        # --- Logic indicators ---#
        # -------------------------#
        interval_expired = minutes_since_change >= self._limit_timeout
        slope_change = abs(vtherm_slope - self._previous_slope) > self._threshold_slope
        is_slope_improving = effective_slope > (self._slope_at_last_change + self._threshold_slope)

        if current_fan is None:
            current_index = 0
        else:
            try:
                current_index = self._fan_modes.index(current_fan)
            except ValueError:
                _LOGGER.debug("Current fan mode %s not in declared modes %s; defaulting to index 0", current_fan, self._fan_modes)
                current_index = 0

        max_index = len(self._fan_modes) - 1
        new_index = current_index
        force = False
        reason = "Unknown"

        # A. EMERGENCY (High real-time error) => highest fan speed immediatly
        if current_temperature_error >= self._hard_error:
            new_index = max_index
            force = True
            reason = f"Emergency: High error ({round(current_temperature_error, 2)}°C)"

        # A-bis. SETPOINT DROP (Target lowered significantly) => lowest fan speed immediately
        # Night mode: when target drops ≥1°C below current (heat) or rises ≥1°C above current (cool)
        elif current_temperature_error < self._threshold_target_drop:
            new_index = 0
            force = True
            reason = f"Setpoint drop: Target moved away ({round(current_temperature_error, 2)}°C)"

        # B. BRAKING ANTICIPATION (Overshoot predicted)
        elif projected_temperature_error < -self._deadband and slope_change:
            new_index = max(0, current_index - 1)
            reason = f"Braking: Target overshoot predicted ({round(projected_temperature, 2)}°C)"

        # C. RECOVERY ANTICIPATION (Under-target predicted)
        elif current_temperature_error > self._soft_error:
            if slope_change or interval_expired:
                if is_slope_improving:
                    reason = "Patience: Trend is improving"
                else:
                    new_index = min(max_index, current_index + 1)
                    intensity = "Strong" if projected_temperature_error > self._projected_error_threshold else "Soft"
                    reason = f"{intensity} recovery: Drop predicted to {round(projected_temperature, 2)}°C"
            else:
                reason = f"Waiting: Observing inertia ({round(minutes_since_change)} min)"

        # D. DRIFT IN COMFORT ZONE
        elif current_temperature_error > 0:
            if (effective_slope < -self._threshold_slope or projected_temperature_error > self._projected_error_threshold) and (slope_change or interval_expired):
                new_index = min(max_index, current_index + 1)
                reason = "Maintenance: Slow drift detected"
            else:
                reason = "Low Active: Observing inertia"

        # E. OVERHEATING / COOLING (ERROR < -DEADBAND)
        elif current_temperature_error < -self._deadband:
            if slope_change or interval_expired:
                new_index = max(0, current_index - 1)
                reason = "Over-target: Reducing speed"
            else:
                reason = "Over-target: Observing inertia"

        # F. COMFORT ZONE (STABLE)
        else:
            if slope_change and effective_slope < -self._deadband:
                new_index = min(max_index, current_index + 1)
                reason = "Comfort: Slow drift detected"
            else:
                reason = "Comfort: Stable"

        # FINAL GUARDS & STEP-DOWN
        final_index = self.determine_final_index(current_index, new_index, minutes_since_change, force)
        target_fan = self._fan_modes[final_index]

        # Update memory
        self.save_states(target_fan, current_fan, vtherm_slope, effective_slope, slope_change)

        # Collect learning data (only normal operating conditions)
        self.learning.add_slope_sample(target_fan, vtherm_slope, current_temperature_error)

        _LOGGER.debug(
            "Decision: hvac=%s current=%.2f target=%.2f err=%.2f proj=%.2f proj_err=%.2f slope=%.3f eff_slope=%.3f accel=%.3f minutes=%.1f -> %s (%s)",
            hvac_mode,
            current_temp,
            target_temp,
            current_temperature_error,
            projected_temperature,
            projected_temperature_error,
            vtherm_slope,
            effective_slope,
            self._thermal_acceleration,
            minutes_since_change,
            target_fan,
            reason,
        )

        return {
            "fan_mode": target_fan,
            "projected_temperature": round(projected_temperature, 2),
            "projected_temperature_error": round(projected_temperature_error, 2),
            "temperature_error": round(current_temperature_error, 2),
            "minutes_since_last_change": round(minutes_since_change, 1),
            "reason": reason
        }
