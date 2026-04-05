"""Constants for Smart Fan Controller."""
from datetime import timedelta

DOMAIN = "smart_fan_controller"
DEVICE_NAME = "Smart Fan Controller"
LEGACY_UNIQUE_ID_PREFIX = "smart_fan"
ENTITY_UNIQUE_ID_PREFIX = DOMAIN
EFFECTIVE_SLOPE_UNIT = "°C/h"
PROFILE_HVAC_MODES = ("heat", "cool")

CONF_CLIMATE_ENTITY = "climate_entity"
CONF_DEADBAND = "deadband"
CONF_MIN_INTERVAL = "min_interval"
CONF_SOFT_ERROR = "soft_error"
CONF_HARD_ERROR = "hard_error"
CONF_LIMIT_TIMEOUT = "limit_timeout"
CONF_LEARNING_ENABLED = "learning_enabled"
CONF_DATA_COLLECTION = "data_collection"
CONF_MPC_SHADOW_ENABLED = "mpc_shadow_enabled"
CONF_DEFROST_ENTITY = "defrost_entity"
CONF_OPERATING_ENTITY = "operating_entity"

# Default values
DEFAULT_DEADBAND = 0.2
DEFAULT_MIN_INTERVAL = 10
DEFAULT_SOFT_ERROR = 0.3
DEFAULT_HARD_ERROR = 0.6
DEFAULT_LIMIT_TIMEOUT = 15
DEFAULT_LEARNING_ENABLED = True
DEFAULT_DATA_COLLECTION = True
DEFAULT_MPC_SHADOW_ENABLED = False

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

LEGACY_OBJECT_KEY_MAP = {
    "reason": "status",
    "fan_mode": "fan_mode",
    "minutes_since_last_change": "fan_mode_last_change",
    "projected_temperature": "temperature_projected_10_min",
    "projected_temperature_error": "temperature_projected_error_10_min",
    "temperature_error": "temperature_error",
    "mpc_shadow_status": "mpc_shadow_status",
    "mpc_shadow_reason": "mpc_shadow_reason",
    "mpc_shadow_fan_mode": "mpc_shadow_fan_mode",
    "mpc_shadow_matches_live": "mpc_shadow_match",
    "mpc_shadow_would_change_now": "mpc_shadow_would_change_now",
    "mpc_shadow_cost": "mpc_shadow_cost",
    "mpc_shadow_confidence": "mpc_shadow_confidence",
    "mpc_shadow_predicted_temperature_10m": "mpc_shadow_predicted_temperature_10_min",
    "mpc_shadow_predicted_temperature_30m": "mpc_shadow_predicted_temperature_30_min",
    "mpc_shadow_dead_time": "mpc_shadow_dead_time",
    "mpc_shadow_known_profiles": "mpc_shadow_known_profiles",
    "mpc_shadow_disturbance_bias": "mpc_shadow_disturbance_bias",
    "learning_progress": "learning_progress",
    "learning_status": "learning_status",
    "learning_samples": "learning_samples",
    "learning_response_events": "learning_response_events",
    "learned_dead_time": "learned_dead_time",
    "effective_timeout": "effective_timeout",
    "mpc_profiles_heat": "mpc_heat_profiles",
    "mpc_profiles_cool": "mpc_cool_profiles",
    "deadband": "learned_deadband",
    "soft_error": "learned_soft_error",
    "hard_error": "learned_hard_error",
    "limit_timeout": "learned_limit_timeout",
    "learning_enabled": "learning_enabled",
    "mpc_shadow_enabled": "mpc_shadow_mode",
}


def build_unique_id(object_key: str, entry_id: str) -> str:
    """Build the canonical unique_id for an entity."""
    return f"{ENTITY_UNIQUE_ID_PREFIX}_{object_key}_{entry_id}"


def build_entity_id(platform_domain: str, object_key: str) -> str:
    """Build the canonical entity_id suggestion for an entity."""
    return f"{platform_domain}.{DOMAIN}_{object_key}"


def extract_object_key_from_unique_id(unique_id: str, entry_id: str) -> str | None:
    """Extract the logical object key from either a legacy or canonical unique_id."""
    suffix = f"_{entry_id}"
    if not unique_id.endswith(suffix):
        return None

    stem = unique_id[: -len(suffix)]
    for prefix in (f"{ENTITY_UNIQUE_ID_PREFIX}_", f"{LEGACY_UNIQUE_ID_PREFIX}_"):
        if stem.startswith(prefix):
            raw_key = stem[len(prefix):]
            return LEGACY_OBJECT_KEY_MAP.get(raw_key, raw_key)

    return None
