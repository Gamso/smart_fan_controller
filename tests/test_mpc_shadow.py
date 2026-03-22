"""Tests for the MPC shadow scaffold."""
import csv
from unittest.mock import patch

from custom_components.smart_fan_controller.controller import SmartFanController
from custom_components.smart_fan_controller.data_collection import DataCollector
from custom_components.smart_fan_controller.mpc_shadow import MPCShadowController
from custom_components.smart_fan_controller.sensor import SmartFanSensor

FAN_MODES = ["low", "medium", "high"]


def _build_controller(min_interval: int = 10) -> SmartFanController:
    return SmartFanController(
        fan_modes=FAN_MODES,
        deadband=0.3,
        min_interval=min_interval,
        soft_error=0.5,
        hard_error=0.9,
        limit_timeout=18,
    )


def _prime_learning_profiles(controller: SmartFanController) -> None:
    for _ in range(10):
        controller.learning.add_slope_sample("low", 0.25, 0.8, "heat")
        controller.learning.add_slope_sample("medium", 0.9, 0.8, "heat")
        controller.learning.add_slope_sample("high", 1.5, 0.8, "heat")
    controller.learning.add_response_event(8.0)
    controller.learning.add_response_event(10.0)
    controller.learning.add_response_event(12.0)


def _prime_cooling_profiles(controller: SmartFanController) -> None:
    for _ in range(10):
        controller.learning.add_slope_sample("low", -0.25, 0.8, "cool")
        controller.learning.add_slope_sample("medium", -0.9, 0.8, "cool")
        controller.learning.add_slope_sample("high", -1.5, 0.8, "cool")
    controller.learning.add_response_event(8.0)
    controller.learning.add_response_event(10.0)
    controller.learning.add_response_event(12.0)


def _prime_marginal_heat_profiles(controller: SmartFanController) -> None:
    for _ in range(10):
        controller.learning.add_slope_sample("low", 0.25, 0.2, "heat")
        controller.learning.add_slope_sample("medium", 0.5, 0.2, "heat")
        controller.learning.add_slope_sample("high", 0.55, 0.2, "heat")
    controller.learning.add_response_event(10.0)


def test_shadow_disabled_reports_disabled_status():
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


def test_shadow_prefers_stronger_fan_when_profiles_support_it():
    controller = _build_controller()
    _prime_learning_profiles(controller)
    controller.last_change_time = controller.now - (20 * 60)
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
    assert result["mpc_shadow_predicted_temperature_30m"] > result["mpc_shadow_predicted_temperature_10m"]


def test_shadow_supports_cooling_profiles():
    controller = _build_controller()
    _prime_cooling_profiles(controller)
    controller.last_change_time = controller.now - (20 * 60)
    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
        enabled=True,
    )

    result = shadow.evaluate(
        current_temp=21.0,
        target_temp=20.0,
        vtherm_slope=-0.25,
        hvac_mode="cool",
        current_fan="low",
        live_decision_fan="medium",
        is_window_open=False,
        minutes_since_change=20.0,
    )

    assert result["mpc_shadow_fan_mode"] == "high"
    assert result["mpc_shadow_matches_live"] == "no"
    assert result["mpc_shadow_would_change_now"] == "yes"
    assert result["mpc_shadow_known_profiles"] == 3
    assert result["mpc_shadow_predicted_temperature_30m"] < result["mpc_shadow_predicted_temperature_10m"]


def test_shadow_hysteresis_holds_current_fan_for_marginal_gain():
    controller = _build_controller()
    _prime_marginal_heat_profiles(controller)
    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
        enabled=True,
    )

    result = shadow.evaluate(
        current_temp=20.0,
        target_temp=20.0,
        vtherm_slope=0.6,
        hvac_mode="heat",
        current_fan="medium",
        live_decision_fan="low",
        is_window_open=False,
        minutes_since_change=20.0,
    )

    assert result["mpc_shadow_fan_mode"] == "medium"
    assert result["mpc_shadow_would_change_now"] == "no"
    assert "Hysteresis holds medium" in result["mpc_shadow_reason"]


def test_shadow_cooling_stops_before_dropping_below_floor():
    controller = SmartFanController(
        fan_modes=["silent", "low", "med", "high"],
        deadband=0.2,
        min_interval=10,
        soft_error=0.3,
        hard_error=0.6,
        limit_timeout=15,
    )
    for _ in range(20):
        controller.learning.add_slope_sample("silent", 0.0, 0.0, "cool")
        controller.learning.add_slope_sample("low", -0.2, 0.2, "cool")
        controller.learning.add_slope_sample("med", -0.5, 0.2, "cool")
        controller.learning.add_slope_sample("high", -1.0, 0.2, "cool")
    controller.learning.add_response_event(10.0)

    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.2,
        min_interval=10,
        fan_modes=["silent", "low", "med", "high"],
        enabled=True,
    )

    result = shadow.evaluate(
        current_temp=20.0,
        target_temp=20.0,
        vtherm_slope=-0.2,
        hvac_mode="cool",
        current_fan="low",
        live_decision_fan="low",
        is_window_open=False,
        minutes_since_change=20.0,
    )

    assert result["mpc_shadow_fan_mode"] == "silent"
    assert result["mpc_shadow_would_change_now"] == "yes"
    assert result["mpc_shadow_predicted_temperature_30m"] >= 19.9


def test_shadow_heating_treats_target_as_floor_too():
    controller = SmartFanController(
        fan_modes=["silent", "low", "med", "high"],
        deadband=0.2,
        min_interval=10,
        soft_error=0.3,
        hard_error=0.6,
        limit_timeout=15,
    )
    for _ in range(20):
        controller.learning.add_slope_sample("silent", 0.0, 0.0, "heat")
        controller.learning.add_slope_sample("low", 0.2, 0.2, "heat")
        controller.learning.add_slope_sample("med", 0.5, 0.2, "heat")
        controller.learning.add_slope_sample("high", 1.0, 0.2, "heat")
    controller.learning.add_response_event(10.0)

    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.2,
        min_interval=10,
        fan_modes=["silent", "low", "med", "high"],
        enabled=True,
    )

    result = shadow.evaluate(
        current_temp=19.8,
        target_temp=20.0,
        vtherm_slope=0.0,
        hvac_mode="heat",
        current_fan="silent",
        live_decision_fan="silent",
        is_window_open=False,
        minutes_since_change=20.0,
    )

    assert result["mpc_shadow_fan_mode"] in ("med", "high")
    assert result["mpc_shadow_would_change_now"] == "yes"


def test_shadow_limits_large_step_down_even_if_lower_mode_is_cheapest():
    fan_modes = ["silent", "low", "med", "high", "superhigh"]
    controller = SmartFanController(
        fan_modes=fan_modes,
        deadband=0.2,
        min_interval=10,
        soft_error=0.3,
        hard_error=0.6,
        limit_timeout=15,
    )
    for _ in range(20):
        controller.learning.add_slope_sample("silent", 0.2, -0.1, "heat")
        controller.learning.add_slope_sample("low", -0.25, -0.1, "heat")
        controller.learning.add_slope_sample("med", 0.7, -0.1, "heat")
        controller.learning.add_slope_sample("high", 1.1, -0.1, "heat")
        controller.learning.add_slope_sample("superhigh", 1.3, -0.1, "heat")
    controller.learning.add_response_event(18.0)

    shadow = MPCShadowController(
        learning=controller.learning,
        deadband=0.2,
        min_interval=10,
        fan_modes=fan_modes,
        enabled=True,
    )

    result = shadow.evaluate(
        current_temp=20.4,
        target_temp=20.3,
        vtherm_slope=1.2,
        hvac_mode="heat",
        current_fan="superhigh",
        live_decision_fan="high",
        is_window_open=False,
        minutes_since_change=60.0,
    )

    assert result["mpc_shadow_fan_mode"] == "high"
    assert result["mpc_shadow_would_change_now"] == "yes"
    assert "Step-down limited to high" in result["mpc_shadow_reason"]


def test_shadow_respects_min_interval_guardrail():
    controller = _build_controller(min_interval=10)
    _prime_learning_profiles(controller)
    controller.last_change_time = controller.now - (2 * 60)
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
        live_decision_fan="low",
        is_window_open=False,
        minutes_since_change=2.0,
    )

    assert result["mpc_shadow_fan_mode"] == "low"
    assert result["mpc_shadow_would_change_now"] == "no"
    assert "Min interval active" in result["mpc_shadow_reason"]


def test_shadow_pauses_when_window_is_open():
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


def test_response_time_learning_skips_window_open_disturbances():
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


def test_shadow_sensor_can_clear_to_none():
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


def test_data_collector_records_shadow_columns(tmp_path):
    collector = DataCollector(str(tmp_path), "entry123456")

    collector.record(
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
