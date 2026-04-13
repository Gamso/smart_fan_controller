"""Constants for Smart Fan Controller."""
from datetime import timedelta

DOMAIN = "smart_fan_controller"
DEVICE_NAME = "Smart Fan Controller"
ENTITY_UNIQUE_ID_PREFIX = DOMAIN
EFFECTIVE_SLOPE_UNIT = "°C/h"
PROFILE_HVAC_MODES = ("heat", "cool")

CONF_CLIMATE_ENTITY = "climate_entity"
CONF_DEADBAND = "deadband"
CONF_MIN_INTERVAL = "min_interval"
CONF_LIMIT_TIMEOUT = "limit_timeout"
CONF_DATA_COLLECTION = "data_collection"
CONF_DEFROST_ENTITY = "defrost_entity"
CONF_OPERATING_ENTITY = "operating_entity"

# Default values
DEFAULT_DEADBAND = 0.2
DEFAULT_MIN_INTERVAL = 10
DEFAULT_LIMIT_TIMEOUT = 15
DEFAULT_DATA_COLLECTION = True

DELTA_TIME_CONTROL_LOOP = 2  # minutes between each control loop execution

# Storage
STORAGE_VERSION = 1
STORAGE_KEY = "smart_fan_controller.learning_data"
LEARNING_DATA_SAVE_INTERVAL = timedelta(minutes=5)

# Controller thresholds
THRESHOLD_SLOPE = 0.1  # °C/h – minimum slope delta to trigger re-evaluation
THRESHOLD_TARGET_DROP = -1.0  # °C  – setpoint drop that triggers immediate speed cut
MAX_PROJECTION_DELTA = 1.0  # °C – maximum temperature change projected in 10 min
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
SETPOINT_DROP_LEARNING_COOLDOWN = 30.0  # Minutes to block learning after a setpoint drop
MIN_ESTABLISHED_RATIO = 2.0  # Minimum factor × dead_time the fan mode must be active before learning


def build_unique_id(object_key: str, entry_id: str) -> str:
    """Build the canonical unique_id for an entity."""
    return f"{ENTITY_UNIQUE_ID_PREFIX}_{object_key}_{entry_id}"


def build_entity_id(platform_domain: str, object_key: str) -> str:
    """Build the canonical entity_id suggestion for an entity."""
    return f"{platform_domain}.{DOMAIN}_{object_key}"
