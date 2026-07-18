"""Tests for Smart Fan Controller config-flow guards."""

from unittest.mock import MagicMock

import voluptuous as vol

from custom_components.smart_fan_controller.config_flow import (
    _airflow_field_key,
    _airflow_schema_fields,
    _extract_fan_modes,
    _is_climate_entity_already_configured,
    _pop_airflow_from_input,
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
    """Only manual fan speeds are offered airflow fields; auto/off are noise here."""
    state = MagicMock()
    state.attributes = {"fan_modes": ["Auto", "silent", "low", "med", "high", "superhigh", "off"]}

    assert _extract_fan_modes(state) == ["silent", "low", "med", "high", "superhigh"]


def test_extract_fan_modes_handles_missing_state() -> None:
    """A climate entity with no state (not yet available) yields no fields, not a crash."""
    assert _extract_fan_modes(None) == []

    state = MagicMock()
    state.attributes = {}
    assert _extract_fan_modes(state) == []


def test_airflow_schema_fields_one_per_fan_mode() -> None:
    """Each detected fan speed gets exactly one optional airflow field."""
    fields = _airflow_schema_fields(["low", "med"], {"low": 300})

    keys = {str(k): k for k in fields}
    assert set(keys) == {_airflow_field_key("low"), _airflow_field_key("med")}
    # The pre-filled default comes from current_airflow; unset fans use vol.UNDEFINED.
    low_key = keys[_airflow_field_key("low")]
    med_key = keys[_airflow_field_key("med")]
    assert low_key.default() == 300
    assert med_key.default is vol.UNDEFINED


def test_pop_airflow_from_input_extracts_and_removes_fields() -> None:
    """Airflow fields are pulled out of the raw form submission, leaving the rest untouched."""
    user_input = {
        _airflow_field_key("low"): 300.0,
        _airflow_field_key("med"): None,  # left empty by the user
        "deadband": 0.2,
    }

    airflow = _pop_airflow_from_input(user_input, ["low", "med", "high"])

    assert airflow == {"low": 300.0}
    assert user_input == {"deadband": 0.2}
