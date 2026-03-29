"""Tests for the MPC shadow diagnostics and guardrails."""

import csv
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.smart_fan_controller.controller import SmartFanController
from custom_components.smart_fan_controller.data_collection import DataCollector
from custom_components.smart_fan_controller.mpc_shadow import MPCShadowController
from custom_components.smart_fan_controller.sensor import SmartFanMpcProfilesSensor, SmartFanSensor

FAN_MODES = ["low", "medium", "high"]


def _build_controller(*, fan_modes=None, min_interval: int = 10) -> SmartFanController:
    return SmartFanController(
        fan_modes=fan_modes or FAN_MODES,
        deadband=0.3,
        min_interval=min_interval,
        soft_error=0.5,
        hard_error=0.9,
        limit_timeout=18,
    )


def _prime_learning_profiles(controller: SmartFanController) -> None:
    for _ in range(60):
        controller.learning.add_slope_sample("low", 0.25, 0.8, "heat")
        controller.learning.add_slope_sample("medium", 0.9, 0.8, "heat")
        controller.learning.add_slope_sample("high", 1.5, 0.8, "heat")
    controller.learning.add_response_event(8.0)
    controller.learning.add_response_event(10.0)
    controller.learning.add_response_event(12.0)


def _make_executor_hass() -> MagicMock:
    hass = MagicMock()

    async def run_in_executor(target, *args):
        return target(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=run_in_executor)
    return hass


def test_shadow_disabled_reports_disabled_status() -> None:
    controller = _build_controller()
    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
        enabled=False,
    )

    result = shadow.evaluate(
        current_temp=19.2,
        target_temp=20.0,
        vtherm_slope=0.4,
        hvac_mode="heat",
        current_fan="medium",
        live_decision_fan="high",
        is_window_open=False,
    )

    assert result["mpc_shadow_status"] == "Disabled"
    assert result["mpc_shadow_fan_mode"] == "medium"
    assert result["mpc_shadow_matches_live"] == "disabled"
    assert result["mpc_shadow_would_change_now"] == "no"


def test_shadow_prefers_stronger_fan_when_profiles_support_it() -> None:
    controller = _build_controller()
    _prime_learning_profiles(controller)
    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
        enabled=True,
    )

    result = shadow.evaluate(
        current_temp=19.0,
        target_temp=20.0,
        vtherm_slope=0.25,
        hvac_mode="heat",
        current_fan="low",
        live_decision_fan="medium",
        is_window_open=False,
        minutes_since_change=20.0,
    )

    assert result["mpc_shadow_fan_mode"] == "high"
    assert result["mpc_shadow_matches_live"] == "no"
    assert result["mpc_shadow_would_change_now"] == "yes"
    assert result["mpc_shadow_known_profiles"] == 3


def test_shadow_holds_superhigh_while_still_below_target() -> None:
    fan_modes = ["low", "medium", "high", "superhigh"]
    controller = _build_controller(fan_modes=fan_modes)
    for _ in range(60):
        controller.learning.add_slope_sample("low", 0.2, 0.4, "heat")
        controller.learning.add_slope_sample("medium", 0.5, 0.4, "heat")
        controller.learning.add_slope_sample("high", 0.8, 0.4, "heat")
        controller.learning.add_slope_sample("superhigh", 1.0, 0.4, "heat")
    controller.learning.add_response_event(30.0)

    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.2,
        min_interval=10,
        fan_modes=fan_modes,
        enabled=True,
    )

    result = shadow.evaluate(
        current_temp=19.95,
        target_temp=20.0,
        vtherm_slope=0.2,
        hvac_mode="heat",
        current_fan="superhigh",
        live_decision_fan="superhigh",
        is_window_open=False,
        minutes_since_change=40.0,
    )

    assert result["mpc_shadow_fan_mode"] == "superhigh"
    assert result["mpc_shadow_would_change_now"] == "no"
    assert "Below target: holding superhigh" in result["mpc_shadow_reason"]


def test_shadow_pauses_when_window_is_open() -> None:
    controller = _build_controller()
    _prime_learning_profiles(controller)
    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
        enabled=True,
    )

    result = shadow.evaluate(
        current_temp=19.3,
        target_temp=20.0,
        vtherm_slope=0.2,
        hvac_mode="heat",
        current_fan="medium",
        live_decision_fan="high",
        is_window_open=True,
        minutes_since_change=12.0,
    )

    assert result["mpc_shadow_status"] == "Disturbed"
    assert result["mpc_shadow_fan_mode"] == "medium"
    assert result["mpc_shadow_would_change_now"] == "no"
    assert "paused" in result["mpc_shadow_reason"]


def test_shadow_sensor_can_clear_to_none() -> None:
    sensor = SmartFanSensor(
        "entry-1",
        "MPC Shadow Cost",
        "mpc_shadow_cost",
        None,
        None,
        "mdi:calculator",
    )

    sensor.update_from_controller({"mpc_shadow_cost": 3.2})
    assert sensor.native_value == 3.2

    sensor.update_from_controller({"mpc_shadow_cost": None})
    assert sensor.native_value is None


def test_mpc_profiles_sensor_exposes_per_mode_values() -> None:
    controller = _build_controller()
    controller.fan_modes = FAN_MODES
    for _ in range(15):
        controller.learning.add_slope_sample("medium", 0.5, 0.3, "heat")
    for _ in range(8):
        controller.learning.add_slope_sample("high", 0.9, 0.3, "heat")
    for _ in range(12):
        controller.learning.add_slope_sample("low", -0.4, 0.3, "cool")

    sensor = SmartFanMpcProfilesSensor("entry-1", controller, "heat")
    attrs = sensor.extra_state_attributes

    assert sensor.native_value == 1
    assert attrs["known_profiles"] == 1
    assert attrs["profiles"]["medium"]["effective_slope"] == 0.5
    assert attrs["profiles"]["medium"]["samples"] == 15
    assert attrs["profiles"]["medium"]["ready"] is True
    assert attrs["profiles"]["high"]["effective_slope"] is None
    assert attrs["profiles"]["high"]["samples"] == 8


def test_live_controller_holds_favorable_slope_until_close_to_target() -> None:
    controller = _build_controller(fan_modes=["low", "high"])
    controller.previous_slope = 0.0
    controller.now = 0.0
    controller.last_change_time = 0.0

    with patch("time.time", return_value=3600.0):
        result = controller.calculate_decision(19.6, 20.0, 0.5, "heat", "high")

    assert result["fan_mode"] == "high"
    assert result["reason"] == "Maintenance: Favorable slope, holding"


def test_response_time_learning_skips_window_open_disturbances() -> None:
    controller = _build_controller()
    for _ in range(250):
        controller.learning.add_slope_sample("medium", 0.3, 0.1)

    base_time = 1_000_000.0

    with patch("time.time", return_value=base_time):
        controller.now = base_time
        controller.save_states("high", "low", 0.5, 0.5, False, is_window_open=False)
        controller.confirm_fan_change()

    events_before = len(controller.learning.response_events)
    with patch("time.time", return_value=base_time + 600):
        controller.now = base_time + 600
        controller.save_states("high", "high", 0.2, 0.2, True, is_window_open=True)

    assert len(controller.learning.response_events) == events_before


@pytest.mark.asyncio
async def test_data_collector_records_shadow_columns(tmp_path: Path) -> None:
    hass = _make_executor_hass()
    collector = DataCollector(hass, str(tmp_path), "entry123456")

    await collector.async_initialize()
    await collector.async_record(
        hvac_mode="heat",
        current_temp=19.0,
        target_temp=20.0,
        vtherm_slope=0.25,
        is_window_open=False,
        decision={
            "temperature_error": 1.0,
            "projected_temperature": 19.2,
            "projected_temperature_error": 0.8,
            "minutes_since_last_change": 20.0,
            "current_fan": "low",
            "fan_mode": "high",
            "reason": "Emergency: High error (1.0C)",
        },
        phase="ESTABLISHED",
        effective_slope=0.25,
        effective_timeout=15.0,
        force=True,
        learning_ready=True,
        dead_time=10.0,
        shadow={
            "mpc_shadow_status": "Ready",
            "mpc_shadow_fan_mode": "high",
            "mpc_shadow_matches_live": "yes",
            "mpc_shadow_would_change_now": "yes",
            "mpc_shadow_cost": 4.321,
            "mpc_shadow_confidence": 75.0,
            "mpc_shadow_predicted_temperature_10m": 19.3,
            "mpc_shadow_predicted_temperature_30m": 19.8,
            "mpc_shadow_known_profiles": 3,
            "mpc_shadow_disturbance_bias": -0.25,
        },
    )

    with open(collector.path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    header = rows[0]
    row = rows[1]

    assert "mpc_shadow_would_change" in header
    assert "mpc_shadow_known_profiles" in header
    assert "mpc_shadow_disturbance" in header
    assert row[header.index("mpc_shadow_would_change")] == "yes"
    assert row[header.index("mpc_shadow_known_profiles")] == "3"
    assert row[header.index("mpc_shadow_disturbance")] == "-0.25"
