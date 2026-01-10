"""Constants for Smart Fan Controller."""

DOMAIN = "smart_fan_controller"

CONF_CLIMATE_ENTITY = "climate_entity"
CONF_DEADBAND = "deadband"
CONF_MIN_INTERVAL = "min_interval"
CONF_SOFT_ERROR = "soft_error"
CONF_HARD_ERROR = "hard_error"
CONF_TEMPERATURE_PROJECTED_ERROR = "temperature_projected_error"

# Valeurs par défaut
DEFAULT_DEADBAND = 0.2
DEFAULT_MIN_INTERVAL = 10
DEFAULT_SOFT_ERROR = 0.3
DEFAULT_HARD_ERROR = 0.6
DEFAULT_TEMPERATURE_PROJECTED_ERROR = 0.5
