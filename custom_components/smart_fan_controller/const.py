"""Constants for Smart Fan Controller."""
from datetime import timedelta

DOMAIN = "smart_fan_controller"

CONF_CLIMATE_ENTITY = "climate_entity"
CONF_DEADBAND = "deadband"
CONF_MIN_INTERVAL = "min_interval"
CONF_SOFT_ERROR = "soft_error"
CONF_HARD_ERROR = "hard_error"
CONF_LIMIT_TIMEOUT = "limit_timeout"
CONF_LEARNING_ENABLED = "learning_enabled"

# Default values
DEFAULT_DEADBAND = 0.2
DEFAULT_MIN_INTERVAL = 10
DEFAULT_SOFT_ERROR = 0.3
DEFAULT_HARD_ERROR = 0.6
DEFAULT_LIMIT_TIMEOUT = 15
DEFAULT_LEARNING_ENABLED = True

DELTA_TIME_CONTROL_LOOP = 2  # minutes between each control loop execution

# Storage
STORAGE_VERSION = 1
STORAGE_KEY = "smart_fan_controller.learning_data"
LEARNING_DATA_SAVE_INTERVAL = timedelta(minutes=5)

# Controller thresholds
THRESHOLD_SLOPE = 0.1  # °C/h – minimum slope delta to trigger re-evaluation
THRESHOLD_TARGET_DROP = -1.0  # °C  – setpoint drop that triggers immediate speed cut
MAX_PROJECTION_DELTA = 2.0  # °C – maximum temperature change projected in 10 min
DEFAULT_DEAD_TIME = 10.0  # minutes – fallback dead time before learning is ready
DEAD_TIME_SAFETY_FACTOR = 1.5  # multiplier applied to learned dead time for effective timeout

# Phase detection
PHASE_DEAD_TIME = "DEAD_TIME"
PHASE_TRANSIENT = "TRANSIENT"
PHASE_ESTABLISHED = "ESTABLISHED"

# Learning
MIN_SAMPLES_LEARNING = 240  # Minimum slope samples required for initial readiness
MIN_LIMIT_TIMEOUT = 5  # Minimum limit_timeout (minutes) derived from learning
MIN_MODE_PROFILE_SAMPLES = 10  # Minimum samples per fan mode to consider profile reliable
