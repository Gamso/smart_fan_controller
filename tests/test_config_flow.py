"""Tests for Smart Fan Controller config-flow guards."""

from unittest.mock import MagicMock

from custom_components.smart_fan_controller.config_flow import _is_climate_entity_already_configured
from custom_components.smart_fan_controller.const import CONF_CLIMATE_ENTITY


def _build_entry(entry_id: str, climate_entity: str, *, options: dict | None = None) -> MagicMock:
    """Return a mock config entry for config-flow guard tests."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {CONF_CLIMATE_ENTITY: climate_entity}
    entry.options = options or {}
    return entry


def test_duplicate_climate_guard_detects_existing_entry() -> None:
    """A climate already present in another entry must be rejected."""
    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(
        return_value=[
            _build_entry(
                "entry-1",
                "climate.old",
                options={CONF_CLIMATE_ENTITY: "climate.living_room"},
            )
        ]
    )

    assert _is_climate_entity_already_configured(hass, "climate.living_room") is True


def test_duplicate_climate_guard_ignores_current_entry() -> None:
    """The current entry must be ignored when editing its options."""
    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(
        return_value=[_build_entry("entry-1", "climate.living_room")]
    )

    assert (
        _is_climate_entity_already_configured(
            hass,
            "climate.living_room",
            exclude_entry_id="entry-1",
        )
        is False
    )