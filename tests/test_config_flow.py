"""Tests for Smart Fan Controller config-flow guards."""

from unittest.mock import MagicMock

from custom_components.smart_fan_controller import _apply_configured_fan_order
from custom_components.smart_fan_controller.config_flow import (
    _extract_fan_modes,
    _is_climate_entity_already_configured,
    _validate_fan_order,
)
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


def test_extract_fan_modes_filters_auto_and_off() -> None:
    """Only manual speeds can be ordered; auto/off are not strength levels."""
    state = MagicMock()
    state.attributes = {"fan_modes": ["Auto", "silent", "low", "med", "high", "superhigh", "off"]}

    assert _extract_fan_modes(state) == ["silent", "low", "med", "high", "superhigh"]


def test_extract_fan_modes_handles_missing_state() -> None:
    """A climate entity with no state yet yields no options, not a crash."""
    assert _extract_fan_modes(None) == []

    state = MagicMock()
    state.attributes = {}
    assert _extract_fan_modes(state) == []


def test_validate_fan_order_accepts_empty_and_complete() -> None:
    """Empty means 'keep the entity's order'; a complete permutation is valid."""
    detected = ["silent", "low", "med"]
    assert _validate_fan_order(None, detected) is None
    assert _validate_fan_order([], detected) is None
    assert _validate_fan_order(["med", "silent", "low"], detected) is None


def test_validate_fan_order_rejects_partial_selection() -> None:
    """A half-specified ladder is rejected rather than silently completed."""
    detected = ["silent", "low", "med"]
    assert _validate_fan_order(["silent", "low"], detected) == "fan_order_incomplete"
    assert _validate_fan_order(["silent", "low", "med", "ghost"], detected) == "fan_order_incomplete"


def test_apply_configured_fan_order_reorders() -> None:
    """The configured order wins over the order reported by the climate entity."""
    detected = ["high", "low", "superhigh", "med"]  # entity reports them jumbled
    configured = ["low", "med", "high", "superhigh"]

    assert _apply_configured_fan_order(detected, configured) == configured


def test_apply_configured_fan_order_without_config_is_passthrough() -> None:
    """No configured order means the detected order is used unchanged."""
    detected = ["low", "med", "high"]

    assert _apply_configured_fan_order(detected, None) == detected
    assert _apply_configured_fan_order(detected, []) == detected


def test_apply_configured_fan_order_tolerates_drift() -> None:
    """A speed added by the entity later is appended; a removed one is dropped."""
    detected = ["low", "med", "high", "turbo"]  # 'turbo' appeared after configuration
    configured = ["low", "med", "high", "retired"]  # 'retired' no longer exists

    assert _apply_configured_fan_order(detected, configured) == ["low", "med", "high", "turbo"]
