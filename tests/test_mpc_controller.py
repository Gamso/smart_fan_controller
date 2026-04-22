"""Tests for the MPC diagnostics and guardrails."""

import csv
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smart_fan_controller.data_collection import DataCollector
from custom_components.smart_fan_controller.mpc_controller import MPCController
from custom_components.smart_fan_controller.sensor import (
    SmartFanMpcProfilesSensor,
    SmartFanProfileEffectiveSlopeSensor,
    SmartFanSensor,
)
from custom_components.smart_fan_controller.thermal_learning import ThermalLearning

FAN_MODES = ["low", "medium", "high"]


def _build_learning(*, fan_modes=None) -> ThermalLearning:  # pylint: disable=unused-argument
    """Build a ThermalLearning instance for test use."""
    return ThermalLearning()


def _build_mpc(learning: ThermalLearning, *, fan_modes=None, min_interval: int = 10) -> MPCController:
    """Build an MPCController with default test parameters."""
    return MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=min_interval,
        limit_timeout=18,
        fan_modes=fan_modes or FAN_MODES,
    )


def _prime_learning_profiles(learning: ThermalLearning) -> None:
    """Feed enough slope samples for all profiles to become ready."""
    for _ in range(60):
        learning.add_slope_sample("low", 0.25, 0.8, "heat")
        learning.add_slope_sample("medium", 0.9, 0.8, "heat")
        learning.add_slope_sample("high", 1.5, 0.8, "heat")
    learning.add_response_event(8.0)
    learning.add_response_event(10.0)
    learning.add_response_event(12.0)


def _make_executor_hass() -> MagicMock:
    """Create a mock HomeAssistant with synchronous executor."""
    hass = MagicMock()

    async def run_in_executor(target, *args):
        """Run target synchronously for tests."""
        return target(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=run_in_executor)
    return hass


def test_mpc_idle_for_unsimulated_hvac_modes() -> None:
    """MPC reports idle status for unsimulated HVAC modes."""
    learning = ThermalLearning()
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    result = mpc.evaluate(
        current_temp=19.2,
        target_temp=20.0,
        vtherm_slope=0.4,
        hvac_mode="off",
        current_fan="medium",
        is_window_open=False,
    )

    assert result["mpc_status"] == "Idle"
    assert result["mpc_fan_mode"] == "medium"
    assert result["mpc_would_change_now"] == "no"


def test_mpc_prefers_stronger_fan_when_profiles_support_it() -> None:
    """MPC picks a stronger fan mode when learned profiles support it."""
    learning = ThermalLearning()
    _prime_learning_profiles(learning)
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    result = mpc.evaluate(
        current_temp=19.0,
        target_temp=20.0,
        vtherm_slope=0.25,
        hvac_mode="heat",
        current_fan="low",
        is_window_open=False,
        minutes_since_change=20.0,
    )

    assert result["mpc_fan_mode"] == "high"
    assert result["mpc_would_change_now"] == "yes"
    assert result["mpc_known_profiles"] == 3


def test_mpc_holds_superhigh_while_still_below_target() -> None:
    """MPC holds superhigh when temperature is still below target."""
    fan_modes = ["low", "medium", "high", "superhigh"]
    learning = ThermalLearning()
    for _ in range(60):
        learning.add_slope_sample("low", 0.2, 0.4, "heat")
        learning.add_slope_sample("medium", 0.5, 0.4, "heat")
        learning.add_slope_sample("high", 0.8, 0.4, "heat")
        learning.add_slope_sample("superhigh", 1.0, 0.4, "heat")
    learning.add_response_event(30.0)

    mpc = MPCController(
        learning=learning,
        deadband=0.2,
        min_interval=10,
        fan_modes=fan_modes,
    )

    result = mpc.evaluate(
        current_temp=19.95,
        target_temp=20.0,
        vtherm_slope=0.2,
        hvac_mode="heat",
        current_fan="superhigh",
        is_window_open=False,
        minutes_since_change=40.0,
    )

    assert result["mpc_fan_mode"] == "superhigh"
    assert result["mpc_would_change_now"] == "no"
    assert "Below target: holding superhigh" in result["mpc_reason"]


def test_mpc_pauses_when_window_is_open() -> None:
    """MPC pauses evaluation when a window is open."""
    learning = ThermalLearning()
    _prime_learning_profiles(learning)
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    result = mpc.evaluate(
        current_temp=19.3,
        target_temp=20.0,
        vtherm_slope=0.2,
        hvac_mode="heat",
        current_fan="medium",
        is_window_open=True,
        minutes_since_change=12.0,
    )

    assert result["mpc_status"] == "Disturbed"
    assert result["mpc_fan_mode"] == "medium"
    assert result["mpc_would_change_now"] == "no"
    assert "paused" in result["mpc_reason"]


def test_mpc_sensor_can_clear_to_none() -> None:
    """MPC sensor value can be cleared to None."""
    sensor = SmartFanSensor(
        "entry-1",
        "climate.living_room",
        "MPC Cost",
        "mpc_cost",
        "mpc_cost",
        None,
        None,
        "mdi:calculator",
    )

    sensor.update_from_mpc({"mpc_cost": 3.2})
    assert sensor.native_value == 3.2

    sensor.update_from_mpc({"mpc_cost": None})
    assert sensor.native_value is None


def test_mpc_profiles_sensor_exposes_per_mode_values() -> None:
    """MPC profiles sensor exposes per-mode effective slope values."""
    learning = ThermalLearning()
    mpc = _build_mpc(learning)
    for _ in range(15):
        learning.add_slope_sample("medium", 0.5, 0.3, "heat")
    for _ in range(8):
        learning.add_slope_sample("high", 0.9, 0.3, "heat")
    for _ in range(12):
        learning.add_slope_sample("low", -0.4, 0.3, "cool")

    sensor = SmartFanMpcProfilesSensor("entry-1", "climate.living_room", mpc, "heat")
    attrs = sensor.extra_state_attributes

    assert sensor.native_value == 1
    assert attrs["known_profiles"] == 1
    assert attrs["profiles"]["medium"]["effective_slope"] == 0.5
    assert attrs["profiles"]["medium"]["samples"] == 15
    assert attrs["profiles"]["medium"]["ready"] is True
    assert attrs["profile_effective_slope_sensors"]["medium"] == "sensor.smart_fan_controller_living_room_heat_medium_effective_slope"
    assert attrs["profiles"]["high"]["effective_slope"] is None
    assert attrs["profiles"]["high"]["samples"] == 8


def test_profile_effective_slope_sensor_exposes_historizable_state() -> None:
    """Profile effective slope sensor is historizable with correct attributes."""
    learning = ThermalLearning()
    mpc = _build_mpc(learning)
    for _ in range(12):
        learning.add_slope_sample("high", 0.9, 0.3, "heat")

    sensor = SmartFanProfileEffectiveSlopeSensor(
        "entry-1",
        "climate.living_room",
        mpc,
        "heat",
        "high",
    )

    assert sensor.entity_id == "sensor.smart_fan_controller_living_room_heat_high_effective_slope"
    assert sensor.native_value == 0.9
    assert sensor.extra_state_attributes["samples"] == 12
    assert sensor.extra_state_attributes["ready"] is True


@pytest.mark.asyncio
async def test_data_collector_records_mpc_columns(tmp_path: Path) -> None:
    """Data collector CSV includes MPC-specific columns."""
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
        mpc_decision={
            "mpc_status": "Ready",
            "mpc_fan_mode": "high",
            "mpc_would_change_now": "yes",
            "mpc_cost": 4.321,
            "mpc_confidence": 75.0,
            "mpc_predicted_temperature_10m": 19.3,
            "mpc_predicted_temperature_30m": 19.8,
            "mpc_known_profiles": 3,
            "mpc_disturbance_bias": -0.25,
        },
    )

    with open(collector.path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    header = rows[0]
    row = rows[1]

    assert "mpc_would_change" in header
    assert "mpc_known_profiles" in header
    assert "mpc_disturbance" in header
    assert row[header.index("mpc_would_change")] == "yes"
    assert row[header.index("mpc_known_profiles")] == "3"
    assert row[header.index("mpc_disturbance")] == "-0.25"


def test_mpc_setpoint_drop_forces_lowest_mode() -> None:
    """When target drops significantly, MPC should go to the lowest fan mode."""
    learning = ThermalLearning()
    _prime_learning_profiles(learning)
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    result = mpc.evaluate(
        current_temp=20.4,
        target_temp=17.5,
        vtherm_slope=0.0,
        hvac_mode="heat",
        current_fan="high",
        is_window_open=False,
        minutes_since_change=5.0,
    )

    assert result["mpc_status"] == "Setpoint drop"
    assert result["mpc_fan_mode"] == "low"
    assert "Setpoint drop" in result["mpc_reason"]


def test_mpc_setpoint_drop_reports_would_change() -> None:
    """Setpoint drop should report would_change correctly."""
    learning = ThermalLearning()
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    result = mpc.evaluate(
        current_temp=20.0,
        target_temp=17.5,
        vtherm_slope=-0.2,
        hvac_mode="heat",
        current_fan="medium",
        is_window_open=False,
        minutes_since_change=15.0,
    )

    assert result["mpc_fan_mode"] == "low"
    assert result["mpc_would_change_now"] == "yes"


def test_mpc_no_setpoint_drop_when_error_above_threshold() -> None:
    """Normal over-target should NOT trigger setpoint drop."""
    learning = ThermalLearning()
    _prime_learning_profiles(learning)
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    result = mpc.evaluate(
        current_temp=20.3,
        target_temp=20.0,
        vtherm_slope=0.0,
        hvac_mode="heat",
        current_fan="high",
        is_window_open=False,
        minutes_since_change=15.0,
    )

    assert result["mpc_status"] != "Setpoint drop"


def test_mpc_pauses_during_defrost() -> None:
    """MPC should pause when defrost is active, like window-open."""
    learning = ThermalLearning()
    _prime_learning_profiles(learning)
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    result = mpc.evaluate(
        current_temp=19.3,
        target_temp=20.0,
        vtherm_slope=0.2,
        hvac_mode="heat",
        current_fan="high",
        is_window_open=False,
        is_defrost_active=True,
        minutes_since_change=12.0,
    )

    assert result["mpc_status"] == "Disturbed"
    assert result["mpc_fan_mode"] == "high"
    assert result["mpc_would_change_now"] == "no"
    assert "Defrost" in result["mpc_reason"]


def test_mpc_disturbance_bias_decays_during_defrost() -> None:
    """Disturbance bias should decay, not update, during defrost."""
    learning = ThermalLearning()
    _prime_learning_profiles(learning)
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    # Prime the disturbance bias with a normal cycle
    mpc.evaluate(
        current_temp=19.5,
        target_temp=20.0,
        vtherm_slope=0.5,
        hvac_mode="heat",
        current_fan="medium",
        is_window_open=False,
        minutes_since_change=20.0,
    )
    bias_before = mpc.disturbance_bias

    # Defrost cycle with sharp slope drop — should NOT poison the bias
    mpc.evaluate(
        current_temp=19.5,
        target_temp=20.0,
        vtherm_slope=-1.0,
        hvac_mode="heat",
        current_fan="high",
        is_window_open=False,
        is_defrost_active=True,
        minutes_since_change=25.0,
    )
    bias_after = mpc.disturbance_bias

    # Bias should have decayed, not grown from the -1.0 slope residual
    assert abs(bias_after) <= abs(bias_before)


def test_monotone_constraint_enforces_ordering() -> None:
    """Monotone constraint should clamp a lower mode's slope up to its neighbor when all profiles are ready."""
    learning = ThermalLearning()

    # Create inverted profiles: silent > med (the real-world bug)
    learning.set_mode_effective_slope("silent", "heat", 0.53)
    learning.set_mode_effective_slope("low", "heat", 0.0)
    learning.set_mode_effective_slope("med", "heat", 0.45)
    learning.set_mode_effective_slope("high", "heat", 0.96)
    learning.set_mode_effective_slope("superhigh", "heat", 1.35)

    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=["silent", "low", "med", "high", "superhigh"],
    )

    monotone = mpc.build_monotone_slopes(["silent", "low", "med", "high", "superhigh"], "heat")
    assert isinstance(monotone, dict)

    # silent (0.53) should stay, low (0.0 → clamped to 0.53), med (0.45 → clamped to 0.53)
    assert monotone["silent"] == pytest.approx(0.53, abs=0.001)
    assert monotone["low"] >= monotone["silent"]
    assert monotone["med"] >= monotone["low"]
    assert monotone["high"] >= monotone["med"]
    assert monotone["superhigh"] >= monotone["high"]


def test_monotone_constraint_returns_partial_dict_for_partial_profiles() -> None:
    """On a fresh install with incomplete profiles, monotone should return an empty dict."""
    learning = ThermalLearning()
    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )
    # No profiles learned yet
    result = mpc.build_monotone_slopes(FAN_MODES, "heat")
    assert isinstance(result, dict)
    assert len(result) == 0


def test_monotone_constraint_partial_profiles_enforces_known_pairs() -> None:
    """With some profiles missing, monotone should still enforce ordering among known ones."""
    learning = ThermalLearning()
    fan_modes = ["silent", "low", "med", "high", "superhigh"]

    # Real-world snapshot inversion seen in the collected data:
    # high=1.59 while superhigh=1.075.
    learning.set_mode_effective_slope("high", "heat", 1.59)
    learning.set_mode_effective_slope("superhigh", "heat", 1.075)

    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=fan_modes,
    )

    result = mpc.build_monotone_slopes(fan_modes, "heat")
    assert isinstance(result, dict)
    # Only known profiles are in the dict
    assert "silent" not in result
    assert "low" not in result
    assert "med" not in result
    assert "high" in result
    assert "superhigh" in result
    assert result["high"] == pytest.approx(1.59, abs=0.001)
    assert result["superhigh"] == pytest.approx(1.59, abs=0.001)


def test_monotone_constraint_noop_when_already_ordered() -> None:
    """When profiles are already monotone, the constraint should not change values."""
    learning = ThermalLearning()

    learning.set_mode_effective_slope("low", "heat", 0.15)
    learning.set_mode_effective_slope("medium", "heat", 0.5)
    learning.set_mode_effective_slope("high", "heat", 1.0)

    mpc = MPCController(
        learning=learning,
        deadband=0.3,
        min_interval=10,
        fan_modes=FAN_MODES,
    )

    monotone = mpc.build_monotone_slopes(FAN_MODES, "heat")
    assert isinstance(monotone, dict)
    assert monotone["low"] == pytest.approx(0.15, abs=0.001)
    assert monotone["medium"] == pytest.approx(0.5, abs=0.001)
    assert monotone["high"] == pytest.approx(1.0, abs=0.001)
