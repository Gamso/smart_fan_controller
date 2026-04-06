"""Tests for async-safe CSV data collection."""

import csv
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.smart_fan_controller import async_setup_entry
from custom_components.smart_fan_controller.const import CONF_CLIMATE_ENTITY, CONF_DATA_COLLECTION
from custom_components.smart_fan_controller.data_collection import DataCollector, _HEADER


def _make_executor_hass() -> MagicMock:
    """Return a hass mock whose executor job runs inline during tests."""
    hass = MagicMock()

    async def run_in_executor(target, *args):
        return target(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=run_in_executor)
    return hass


@pytest.mark.asyncio
async def test_async_initialize_creates_header(tmp_path: Path) -> None:
    """The collector should create its CSV header via the executor."""
    hass = _make_executor_hass()
    collector = DataCollector(hass, str(tmp_path), "123456789")

    await collector.async_initialize()

    with open(collector.path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    assert rows == [_HEADER]
    hass.async_add_executor_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_record_appends_row(tmp_path: Path) -> None:
    """The collector should append a data row after initialization."""
    hass = _make_executor_hass()
    collector = DataCollector(hass, str(tmp_path), "123456789")

    await collector.async_initialize()
    await collector.async_record(
        hvac_mode="heat",
        current_temp=20.1234,
        target_temp=21.0,
        vtherm_slope=-0.2468,
        is_window_open=False,
        decision={
            "temperature_error": 0.8765,
            "projected_temperature": 20.5,
            "projected_temperature_error": 0.5,
            "minutes_since_last_change": 12.345,
            "current_fan": "low",
            "fan_mode": "medium",
            "reason": "Strong recovery",
        },
        phase="TRANSIENT",
        effective_slope=-0.2468,
        effective_timeout=15.4321,
        force=True,
        learning_ready=False,
        dead_time=10.987,
    )

    with open(collector.path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    assert len(rows) == 2
    assert rows[0] == _HEADER
    assert rows[1][0].endswith("Z")
    assert rows[1][1:] == [
        "heat",
        "20.123",
        "21.0",
        "0.876",
        "-0.2468",
        "-0.2468",
        "20.5",
        "0.5",
        "TRANSIENT",
        "12.35",
        "15.43",
        "low",
        "medium",
        "1",
        "Strong recovery",
        "0",
        "10.99",
        "0",
        "0",
        "0",
    ]
    assert hass.async_add_executor_job.await_count == 2


@pytest.mark.asyncio
async def test_async_initialize_rotates_file_on_header_change(tmp_path: Path) -> None:
    """The collector should rotate the active CSV when the schema changes."""
    legacy_path = tmp_path / "smart_fan_controller_data_12345678.csv"
    rotated_path = tmp_path / "smart_fan_controller_data_12345678_old.csv"
    legacy_path.write_text("timestamp,hvac_mode\n2026-01-01T00:00:00Z,heat\n", encoding="utf-8")

    hass = _make_executor_hass()
    collector = DataCollector(hass, str(tmp_path), "123456789")

    await collector.async_initialize()

    with open(collector.path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    assert rows == [_HEADER]
    assert rotated_path.exists()


@pytest.mark.asyncio
async def test_async_setup_entry_initializes_data_collector() -> None:
    """Integration setup should await collector initialization before continuing."""
    hass = MagicMock()
    hass.data = {}
    hass.config = MagicMock()
    hass.config.config_dir = "/tmp"
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.services = MagicMock()
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())

    entry = MagicMock()
    entry.entry_id = "123456789"
    entry.data = {
        CONF_CLIMATE_ENTITY: "climate.test",
        CONF_DATA_COLLECTION: True,
    }
    entry.options = {}
    entry.async_on_unload = MagicMock()

    fake_store = AsyncMock()
    fake_store.async_load = AsyncMock(return_value=None)
    fake_store.async_save = AsyncMock()

    fake_collector = MagicMock()
    fake_collector.path = "/tmp/smart_fan_controller_data_12345678.csv"
    fake_collector.async_initialize = AsyncMock()

    with patch("custom_components.smart_fan_controller.Store", return_value=fake_store):
        with patch("custom_components.smart_fan_controller.DataCollector", return_value=fake_collector) as collector_cls:
            with patch("custom_components.smart_fan_controller.async_track_time_interval", return_value=MagicMock()):
                with patch("custom_components.smart_fan_controller.async_track_state_change_event", return_value=MagicMock()):
                    result = await async_setup_entry(hass, entry)

    assert result is True
    collector_cls.assert_called_once_with(hass, hass.config.config_dir, entry.entry_id)
    fake_collector.async_initialize.assert_awaited_once()
