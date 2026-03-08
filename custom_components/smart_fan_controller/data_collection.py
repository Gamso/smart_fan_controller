"""Data collection module for Smart Fan Controller.

Writes one CSV row per control cycle so that algorithm behaviour can be
reproduced offline and used to identify improvement areas during the beta.

CSV columns (in order):
  timestamp, hvac_mode, current_temp, target_temp, current_error,
  vtherm_slope, effective_slope, projected_temp, projected_error,
  phase, minutes_since_change, effective_timeout, current_fan, decided_fan,
  force, reason, learning_ready, dead_time, is_window_open
"""

import csv
import logging
import os
from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)

# Header used when creating a new file.  Keep in sync with _row() below.
_HEADER = [
    "timestamp",
    "hvac_mode",
    "current_temp",
    "target_temp",
    "current_error",
    "vtherm_slope",
    "effective_slope",
    "projected_temp",
    "projected_error",
    "phase",
    "minutes_since_change",
    "effective_timeout",
    "current_fan",
    "decided_fan",
    "force",
    "reason",
    "learning_ready",
    "dead_time",
    "is_window_open",
]

# Rotate the file when it exceeds this size (bytes).  10 MB keeps ~200 000 rows.
_MAX_FILE_SIZE = 10 * 1024 * 1024


class DataCollector:
    """Appends one CSV row per control cycle to a rotating log file."""

    def __init__(self, config_dir: str, entry_id: str) -> None:
        self._path = os.path.join(config_dir, f"smart_fan_controller_data_{entry_id[:8]}.csv")
        self._rotated_path = self._path.replace(".csv", "_old.csv")
        self._ensure_header()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        hvac_mode: str,
        current_temp: float,
        target_temp: float,
        vtherm_slope: float,
        is_window_open: bool,
        decision: dict,
        phase: str,
        effective_slope: float,
        effective_timeout: float,
        force: bool,
        learning_ready: bool,
        dead_time: float,
    ) -> None:
        """Append one row to the CSV file."""
        row = [
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            hvac_mode,
            round(current_temp, 3),
            round(target_temp, 3),
            round(decision.get("temperature_error", 0.0), 3),
            round(vtherm_slope, 4),
            round(effective_slope, 4),
            round(decision.get("projected_temperature", current_temp), 3),
            round(decision.get("projected_temperature_error", 0.0), 3),
            phase,
            round(decision.get("minutes_since_last_change", 0.0), 2),
            round(effective_timeout, 2),
            decision.get("current_fan", ""),
            decision.get("fan_mode", ""),
            int(force),
            decision.get("reason", ""),
            int(learning_ready),
            round(dead_time, 2),
            int(is_window_open),
        ]
        try:
            self._rotate_if_needed()
            with open(self._path, "a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(row)
        except OSError as exc:
            _LOGGER.warning("DataCollector: could not write to %s: %s", self._path, exc)

    @property
    def path(self) -> str:
        """Return the active CSV file path."""
        return self._path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_header(self) -> None:
        """Write the header row if the file does not exist yet."""
        if not os.path.exists(self._path):
            try:
                with open(self._path, "w", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerow(_HEADER)
                _LOGGER.info("DataCollector: created %s", self._path)
            except OSError as exc:
                _LOGGER.warning("DataCollector: could not create %s: %s", self._path, exc)

    def _rotate_if_needed(self) -> None:
        """Rename current file to *_old.csv when it exceeds _MAX_FILE_SIZE."""
        try:
            if os.path.exists(self._path) and os.path.getsize(self._path) >= _MAX_FILE_SIZE:
                if os.path.exists(self._rotated_path):
                    os.remove(self._rotated_path)
                os.rename(self._path, self._rotated_path)
                self._ensure_header()
                _LOGGER.info("DataCollector: rotated %s → %s", self._path, self._rotated_path)
        except OSError as exc:
            _LOGGER.warning("DataCollector: rotation error for %s: %s", self._path, exc)
