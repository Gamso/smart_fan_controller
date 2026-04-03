"""Tests for entity naming migration."""

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smart_fan_controller import _async_migrate_entity_registry
from custom_components.smart_fan_controller.const import DOMAIN


@pytest.mark.asyncio
async def test_entity_registry_migration_renames_legacy_entities(hass) -> None:
    """Legacy smart_fan_* entities should be migrated to smart_fan_controller_*."""
    registry = er.async_get(hass)
    await registry.async_load()

    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry123")
    entry.add_to_hass(hass)

    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "smart_fan_reason_entry123",
        config_entry=entry,
        suggested_object_id="Status",
        original_name="Status",
        has_entity_name=False,
    )
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "smart_fan_learned_dead_time_entry123",
        config_entry=entry,
        suggested_object_id="Learned Dead Time",
        original_name="Learned Dead Time",
        has_entity_name=False,
    )
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        "smart_fan_learning_enabled_entry123",
        config_entry=entry,
        suggested_object_id="Learning Enabled",
        original_name="Learning Enabled",
        has_entity_name=False,
    )

    await _async_migrate_entity_registry(hass, entry)

    status = registry.async_get("sensor.smart_fan_controller_status")
    learned_dead_time = registry.async_get("sensor.smart_fan_controller_learned_dead_time")
    learning_enabled = registry.async_get("switch.smart_fan_controller_learning_enabled")

    assert status is not None
    assert status.unique_id == "smart_fan_controller_status_entry123"
    assert status.has_entity_name is True

    assert learned_dead_time is not None
    assert learned_dead_time.unique_id == "smart_fan_controller_learned_dead_time_entry123"
    assert learned_dead_time.has_entity_name is True

    assert learning_enabled is not None
    assert learning_enabled.unique_id == "smart_fan_controller_learning_enabled_entry123"
    assert learning_enabled.has_entity_name is True

    assert registry.async_get("sensor.status") is None
    assert registry.async_get("sensor.learned_dead_time") is None
    assert registry.async_get("switch.learning_enabled") is None
