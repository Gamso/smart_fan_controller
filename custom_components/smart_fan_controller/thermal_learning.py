import logging
import time
import statistics

from .const import MIN_SAMPLES_LEARNING, MIN_LIMIT_TIMEOUT, MIN_MODE_PROFILE_SAMPLES, DEFAULT_DEAD_TIME

_LOGGER = logging.getLogger(__name__)


class ThermalLearning:
    """Auto-calibration of thermal parameters based on observed system behavior."""

    def __init__(self):
        # Data collection with sliding window
        self._slope_samples = []  # (timestamp, fan_mode, slope, hvac_mode)
        self._response_events = []  # (timestamp, response_time_minutes) - thermal response from fan change to slope change
        self._learning_window_hours = 168  # 7 days sliding window
        self._min_samples = MIN_SAMPLES_LEARNING  # Minimum samples for initial readiness (48-72h typical activity)
        self._ready_once = False  # Flag to track if we've ever reached ready state

        # Incremental statistics using Welford's algorithm
        self._slope_count = 0  # Number of slope samples processed
        self._slope_mean = 0.0  # Running mean of absolute slopes
        self._slope_m2 = 0.0  # Sum of squared differences for variance
        self._slope_max = 0.0  # Maximum absolute slope

        # Cache for computed optimal parameters (invalidated on each new sample)
        self._optimal_cache: dict | None = None

    def reset(self) -> None:
        """Reset all learning data and statistics."""
        self._slope_samples.clear()
        self._response_events.clear()
        self._slope_count = 0
        self._slope_mean = 0.0
        self._slope_m2 = 0.0
        self._slope_max = 0.0
        self._ready_once = False
        self._optimal_cache = None
        _LOGGER.info("Learning: reset requested; data cleared")

    def add_slope_sample(self, fan_mode: str, slope: float, temperature_error: float = 0, hvac_mode: str = "unknown", is_window_open: bool = False):
        """Record slope only if in normal operating range.

        Samples are filtered out when:
        - Setpoint drop / night mode (error < -1°C)
        - Window is open (external disturbance, not representative)
        - Slope is stagnant (no useful data)
        """
        if temperature_error < -1.0:
            _LOGGER.debug("Learning: Skipped sample (setpoint drop, err=%.2f)", temperature_error)
            return

        if is_window_open:
            _LOGGER.debug("Learning: Skipped sample (window open)")
            return

        if abs(slope) < 0.15:
            _LOGGER.debug("Learning: Skipped sample (stagnation, slope=%.2f)", slope)
            return

        self._slope_samples.append((time.time(), fan_mode, slope, hvac_mode))
        _LOGGER.debug("Learning: Collected slope sample #%d (fan=%s, slope=%.2f, err=%.2f, hvac=%s)", len(self._slope_samples), fan_mode, slope, temperature_error, hvac_mode)

        self._optimal_cache = None

        self._update_slope_stats(abs(slope))

        # Cleanup: keep only data within sliding window (7 days)
        cutoff_time = time.time() - (self._learning_window_hours * 3600)
        before = len(self._slope_samples)
        self._slope_samples = [s for s in self._slope_samples if s[0] > cutoff_time]

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

        # Invalidate cached optimal parameters
        self._optimal_cache = None

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
        """Return learning progress as percentage (0-100).

        Once is_ready() has been reached (based on _ready_once), this always
        returns 100.0 so the UI remains consistent even if the sliding window
        later drops below _min_samples.
        """
        if self._ready_once:
            return 100.0
        sample_count = len(self._slope_samples)
        return min(100.0, (sample_count / self._min_samples) * 100)

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
    def slope_samples(self) -> list:
        """Return the list of slope samples."""
        return self._slope_samples

    @slope_samples.setter
    def slope_samples(self, value: list) -> None:
        self._slope_samples = value
        self._optimal_cache = None

    @property
    def response_events(self) -> list:
        """Return the list of response events."""
        return self._response_events

    @response_events.setter
    def response_events(self, value: list) -> None:
        self._response_events = value
        self._optimal_cache = None

    @property
    def optimal_cache(self) -> dict | None:
        """Return the cached optimal parameters, or None if not yet computed."""
        return self._optimal_cache

    def is_ready(self) -> bool:
        """Check if enough data has been collected."""
        if not self._ready_once and len(self._slope_samples) >= self._min_samples:
            self._ready_once = True
            _LOGGER.info("Learning: Initial readiness reached with %d samples", self._min_samples)
        return self._ready_once

    def get_dead_time(self) -> float:
        """Return the learned dead time (median response time) in minutes.

        Falls back to DEFAULT_DEAD_TIME when no response events have been recorded yet.
        """
        response_times = [t for _, t in self._response_events if t > 0]
        if not response_times:
            return DEFAULT_DEAD_TIME
        return statistics.median(response_times)

    def get_mode_effective_slope(self, fan_mode: str, hvac_mode: str) -> float | None:
        """Return the average effective slope for a fan mode in a given HVAC mode.

        Effective slope is positive when moving towards target:
        - In heating: positive raw slope is good
        - In cooling: negative raw slope is good (inverted)

        Returns None if fewer than MIN_MODE_PROFILE_SAMPLES are available.
        """
        matching_slopes = [sl for (_, fm, sl, hm) in self._slope_samples if fm == fan_mode and hm == hvac_mode]
        if len(matching_slopes) < MIN_MODE_PROFILE_SAMPLES:
            return None
        avg = statistics.mean(matching_slopes)
        return -avg if hvac_mode == "cool" else avg

    def compute_optimal_parameters(self) -> dict:
        """Calculate optimal parameters from learned data.

        The result is cached and invalidated whenever a new sample or response
        event is recorded, so repeated property accesses from sensors have
        zero recomputation cost.
        """
        if not self.is_ready():
            return {}

        if self._optimal_cache is not None:
            return self._optimal_cache

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

        # Compute optimal_limit_timeout based on observed response time.
        # Apply a minimum floor (MIN_LIMIT_TIMEOUT) to prevent the timeout from
        # being set so low that it causes continuous fan oscillations.
        optimal_limit_timeout = max(MIN_LIMIT_TIMEOUT, int(round(avg_response)))

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

        result = {
            "deadband": round(optimal_deadband, 2),
            "soft_error": round(optimal_soft_error, 2),
            "hard_error": round(optimal_hard_error, 2),
            "limit_timeout": optimal_limit_timeout,
            "samples_count": self._slope_count,
            "response_samples": len(response_times),
        }
        self._optimal_cache = result
        return result

    def to_dict(self) -> dict:
        """Serialize for storage.

        Both collections are capped to prevent unbounded storage growth:
        - slope_samples: last 5 000 entries (~7 days at 2-min intervals)
        - response_events: last 100 entries (more than enough for statistics)
        """
        return {
            "slope_samples": self._slope_samples[-5000:],
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
        self._optimal_cache = None  # Invalidate cache when stats are rebuilt

        for sample in self._slope_samples:
            self._update_slope_stats(abs(sample[2]))

        _LOGGER.debug(
            "Learning: Recomputed stats from window (count=%d, mean=%.3f, max=%.3f)",
            self._slope_count,
            self._slope_mean,
            self._slope_max,
        )

    @classmethod
    def from_dict(cls, data: dict):
        """Restore from storage.

        Handles backward compatibility: old 3-tuple samples (timestamp, fan_mode, slope)
        are migrated to 4-tuples by appending hvac_mode="unknown".
        """
        instance = cls()

        # Migrate slope_samples: support both 3-tuple (old) and 4-tuple (new)
        raw_samples = data.get("slope_samples", [])
        instance._slope_samples = []
        for sample in raw_samples:
            if len(sample) == 3:
                instance._slope_samples.append((sample[0], sample[1], sample[2], "unknown"))
            else:
                instance._slope_samples.append(tuple(sample))

        instance._response_events = data.get("response_events", [])

        # Restore incremental statistics
        instance._slope_count = data.get("slope_count", 0)
        instance._slope_mean = data.get("slope_mean", 0.0)
        instance._slope_m2 = data.get("slope_M2", 0.0)
        instance._slope_max = data.get("slope_max", 0.0)

        # Apply sliding window cleanup on restore
        cutoff_time = time.time() - (instance._learning_window_hours * 3600)
        instance._slope_samples = [s for s in instance._slope_samples if s[0] > cutoff_time]
        instance._response_events = [(ts, t) for ts, t in instance._response_events if ts > cutoff_time]

        # Rebuild stats from cleaned window
        instance.recompute_slope_stats()

        # Mark as ready once if we have enough data
        if instance._slope_count >= instance._min_samples:
            instance._ready_once = True

        instance._optimal_cache = None

        return instance
