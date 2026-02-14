import logging
import time

from .const import (
    DELTA_TIME_CONTROL_LOOP
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
            # Fan is changing - record the timestamp for future response time calculation
            self._last_change_time = self._now
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

    def calculate_decision(self, current_temp: float, target_temp: float, vtherm_slope: float, hvac_mode: str, current_fan: str | None) -> dict:
        """Compute new fan speed."""
        self._now = time.time()

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
            elif interval_expired:
                # Proactive adjustment: stable away from target but interval expired
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

        # Collect learning data (only normal operating conditions and when learning is enabled)
        if self.learning_enabled:
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
