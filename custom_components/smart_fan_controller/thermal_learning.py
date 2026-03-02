import logging
import time
import statistics

_LOGGER = logging.getLogger(__name__)


class ThermalLearning:
    """Auto-calibration of thermal parameters based on observed system behavior."""

    def __init__(self):
        # Data collection with sliding window
        self._slope_samples = []  # (timestamp, fan_mode, slope)
        self._response_events = []  # (timestamp, response_time_minutes) - thermal response from fan change to slope change
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
            return  # Skip: Setpoint change, night, or emergency conditions

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

    @property
    def slope_count(self) -> int:
        """Return the number of slope samples processed."""
        return self._slope_count

    @property
    def slope_mean(self) -> float:
        """Return the running mean of absolute slopes."""
        return self._slope_mean

    @property
    def slope_m2(self) -> float:
        """Return the sum of squared differences for variance."""
        return self._slope_m2

    @property
    def slope_max(self) -> float:
        """Return the maximum absolute slope."""
        return self._slope_max

    @property
    def min_samples(self) -> int:
        """Return the minimum samples required for readiness."""
        return self._min_samples

    @property
    def response_events(self) -> list:
        """Return the list of response events."""
        return self._response_events

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
        # Use median instead of mean to be robust against outliers
        avg_response = statistics.median(response_times) if response_times else 10.0

        # Compute optimal_limit_timeout based on observed response time
        # Use the median response time directly for maximum responsiveness
        # No artificial bounds - let the observed system behavior dictate the timeout
        optimal_limit_timeout = int(round(avg_response))

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
            "slope_samples": list(self._slope_samples),
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
