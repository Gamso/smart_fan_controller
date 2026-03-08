import logging
import time
from typing import Callable

from .const import (
    THRESHOLD_SLOPE,
    THRESHOLD_TARGET_DROP,
    MAX_PROJECTION_DELTA,
    DEFAULT_DEAD_TIME,
    DEAD_TIME_SAFETY_FACTOR,
    PHASE_DEAD_TIME,
    PHASE_TRANSIENT,
    PHASE_ESTABLISHED,
)
from .thermal_learning import ThermalLearning

_LOGGER = logging.getLogger(__name__)


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
        learning_enabled: bool = True,
        clock: Callable[[], float] = time.time,
    ):
        # Initialize the attribute even if it is None initially
        self._fan_modes: list | None = fan_modes

        # Config Flow values
        self._deadband = deadband
        self._min_interval = min_interval
        self._soft_error = soft_error
        self._hard_error = hard_error
        self._limit_timeout = limit_timeout

        # Injectable clock – can override for isolated unit tests.
        # Note: calculate_decision still calls time.time() directly so that
        # unittest.mock.patch('time.time', ...) continues to work in tests.
        self._clock = clock

        # State variables
        self._previous_slope: float | None = None
        self._slope_at_last_change: float = 0.0
        self._now: float = time.time()
        self._last_change_time: float = self._now - (self._limit_timeout * 60)
        self._last_slope_significant_change: float = self._now
        self._last_hvac_mode: str | None = None

        # Learning system
        self.learning_enabled = learning_enabled
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
        """Estimate temperature projection in 10 min using a linear model.

        The VTherm slope is already smoothed over ~15-30 min (EMA). A second-order
        (parabolic) term added negligible accuracy but amplified noise through a
        double derivative of an already-filtered signal. A clamped linear projection
        is simpler and more robust.
        """
        window_time = 10 / 60  # hours
        temp_proj = current_temp + (vtherm_slope * window_time)

        # Clamp to physically reasonable range
        temp_proj = max(current_temp - MAX_PROJECTION_DELTA, min(current_temp + MAX_PROJECTION_DELTA, temp_proj))
        return temp_proj

    def _get_effective_timeout(self) -> float:
        """Return the adaptive timeout (minutes) based on learned dead time.

        Uses the learned median response time × safety factor so the system
        waits long enough for a fan change to materialize at the sensor.
        Falls back to the configured limit_timeout when learning is not ready.
        """
        if self.learning_enabled and self.learning.is_ready():
            learned_dead_time = self.learning.get_dead_time()
            return max(self._min_interval, learned_dead_time * DEAD_TIME_SAFETY_FACTOR)
        return self._limit_timeout

    def _detect_phase(self, minutes_since_change: float) -> str:
        """Classify the current control phase relative to the last fan change.

        - DEAD_TIME:    Too early for the sensor to see the effect of the change.
        - TRANSIENT:    The sensor is starting to react but hasn't stabilized.
        - ESTABLISHED:  The slope now reflects the current fan regime.
        """
        learned_dead_time = self.learning.get_dead_time() if self.learning_enabled and self.learning.is_ready() else DEFAULT_DEAD_TIME
        if minutes_since_change < learned_dead_time:
            return PHASE_DEAD_TIME
        if minutes_since_change < learned_dead_time * DEAD_TIME_SAFETY_FACTOR:
            return PHASE_TRANSIENT
        return PHASE_ESTABLISHED

    def _apply_step_limit(self, current_index: int, new_index: int) -> int:
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
            return self._apply_step_limit(current_index, new_index)

        # Enforce the minimum time between two changes
        if minutes_since_change < self._min_interval:
            return current_index

        return self._apply_step_limit(current_index, new_index)

    def record_manual_override(self, new_fan: str) -> dict:
        """Persist last change timestamp and return manual override payload."""
        self._last_change_time = time.time()

        return {
            "fan_mode": new_fan,
            "minutes_since_last_change": 0.0,
            "reason": "Manual Override"
        }

    def confirm_fan_change(self) -> None:
        """Update the last-change timestamp once HA confirms the fan speed change.

        Uses self._now (the clock snapshot taken at the start of the last
        calculate_decision cycle) rather than calling the clock a second time.
        This keeps the cooldown timer aligned with the decision cycle and makes
        the method compatible with tests that set self._now directly.
        """
        self._last_change_time = self._now

    def save_states(self, target_fan: str, current_fan: str | None, vtherm_slope: float, effective_slope: float, slope_change: bool):
        """Update states."""
        if target_fan != current_fan:
            # Record the slope snapshot at the moment of the decision.
            # Note: _last_change_time is updated by confirm_fan_change() AFTER the
            # HA service call succeeds, to avoid advancing the cooldown on failed calls.
            self._slope_at_last_change = effective_slope

        if target_fan != current_fan or slope_change:
            self._previous_slope = vtherm_slope

        # Track significant slope changes for response time calculation
        # Response time = time from last fan change to this slope change (thermal response)
        if slope_change and self._last_change_time > 0:
            response_time = (self._now - self._last_change_time) / 60
            # Only record reasonable response times (between 2 and 60 minutes)
            # Very short times might be noise, very long times might be system off or other issues
            if 2.0 <= response_time <= 60.0 and self.learning_enabled:
                self.learning.add_response_event(response_time)
            # Track when the last slope change occurred (for reference, not used in calculation)
            self._last_slope_significant_change = self._now

    def calculate_decision(self, current_temp: float, target_temp: float, vtherm_slope: float, hvac_mode: str, current_fan: str | None, is_window_open: bool = False) -> dict:
        """Compute new fan speed."""
        self._now = time.time()

        # Early exit: unsupported or inactive HVAC modes
        if hvac_mode in ("off", "dry", "fan_only"):
            return {
                "fan_mode": current_fan,
                "projected_temperature": round(current_temp, 2),
                "projected_temperature_error": 0.0,
                "temperature_error": 0.0,
                "minutes_since_last_change": round((self._now - self._last_change_time) / 60, 1),
                "reason": f"HVAC mode '{hvac_mode}': holding current speed",
            }

        # Reset thermal memory when HVAC mode switches (heat ↔ cool)
        if self._last_hvac_mode is not None and self._last_hvac_mode != hvac_mode:
            _LOGGER.info("HVAC mode changed from %s to %s: resetting thermal state", self._last_hvac_mode, hvac_mode)
            self._previous_slope = vtherm_slope
        self._last_hvac_mode = hvac_mode

        # Init slope states
        if self._previous_slope is None:
            self._previous_slope = vtherm_slope
            self._slope_at_last_change = vtherm_slope

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

        # Return early if fan modes not initialized, but include all sensor data
        if not self._fan_modes:
            _LOGGER.warning("Fan modes are not initialized; holding current mode %s", current_fan)
            return {
                "fan_mode": current_fan,
                "projected_temperature": round(projected_temperature, 2),
                "projected_temperature_error": round(projected_temperature_error, 2),
                "temperature_error": round(current_temperature_error, 2),
                "minutes_since_last_change": round(minutes_since_change, 1),
                "reason": "No fan modes defined"
            }

        # -------------------------#
        # --- Logic indicators ---#
        # -------------------------#
        effective_timeout = self._get_effective_timeout()
        interval_expired = minutes_since_change >= effective_timeout
        slope_change = abs(vtherm_slope - self._previous_slope) > THRESHOLD_SLOPE
        is_slope_improving = effective_slope > (self._slope_at_last_change + THRESHOLD_SLOPE)
        phase = self._detect_phase(minutes_since_change)

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
        elif current_temperature_error < THRESHOLD_TARGET_DROP:
            new_index = 0
            force = True
            reason = f"Setpoint drop: Target moved away ({round(current_temperature_error, 2)}°C)"

        # B. BRAKING ANTICIPATION (Overshoot predicted)
        elif projected_temperature_error < -self._deadband and slope_change:
            new_index = max(0, current_index - 1)
            reason = f"Braking: Target overshoot predicted ({round(projected_temperature, 2)}°C)"

        # C. RECOVERY ANTICIPATION (Under-target predicted)
        elif current_temperature_error > self._soft_error:
            if phase == PHASE_DEAD_TIME:
                reason = "Patience: Waiting for thermal response"
            elif slope_change or interval_expired:
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
            # Descent: strong favorable slope in established phase → reduce fan
            if effective_slope > THRESHOLD_SLOPE * 2 and phase == PHASE_ESTABLISHED and interval_expired:
                new_index = max(0, current_index - 1)
                reason = "Maintenance: Strong favorable slope, reducing"
            elif (effective_slope < -THRESHOLD_SLOPE or projected_temperature_error > self._projected_error_threshold) and (slope_change or interval_expired):
                new_index = min(max_index, current_index + 1)
                reason = "Maintenance: Slow drift detected"
            elif interval_expired and phase == PHASE_ESTABLISHED:
                new_index = min(max_index, current_index + 1)
                reason = "Maintenance: Stable away from target, reaching setpoint"
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
            if slope_change and effective_slope < -THRESHOLD_SLOPE:
                new_index = min(max_index, current_index + 1)
                reason = "Comfort: Slow drift detected"
            else:
                reason = "Comfort: Stable"

        # FINAL GUARDS & STEP-DOWN
        final_index = self.determine_final_index(current_index, new_index, minutes_since_change, force)
        target_fan = self._fan_modes[final_index]

        # Update memory
        self.save_states(target_fan, current_fan, vtherm_slope, effective_slope, slope_change)

        # Collect learning data using the fan mode CURRENTLY active (not the decided one),
        # so the slope observation is correctly attributed to the mode that produced it.
        if self.learning_enabled and current_fan is not None and current_fan in self._fan_modes:
            self.learning.add_slope_sample(current_fan, vtherm_slope, current_temperature_error, hvac_mode, is_window_open)

        _LOGGER.debug(
            "Decision: hvac=%s current=%.2f target=%.2f err=%.2f proj=%.2f proj_err=%.2f slope=%.3f eff_slope=%.3f phase=%s minutes=%.1f -> %s (%s)",
            hvac_mode,
            current_temp,
            target_temp,
            current_temperature_error,
            projected_temperature,
            projected_temperature_error,
            vtherm_slope,
            effective_slope,
            phase,
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
