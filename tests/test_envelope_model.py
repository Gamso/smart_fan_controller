"""Tests for the grey-box envelope model and the feasibility gate.

The envelope model fits dT/dt = k_env·(T_ext − T) + u_fan to separate the
fan-independent envelope conductance from each fan's own cooling power, and
drives a feasibility gate that excludes speeds which cannot hold the setpoint at
the current outdoor temperature. See docs/effective_slope_analysis.md.
"""
import random

from custom_components.smart_fan_controller import mpc_controller as mpc_module
from custom_components.smart_fan_controller.thermal_learning import ThermalLearning
from custom_components.smart_fan_controller.mpc_controller import MPCController

FAN_MODES = ["low", "med", "high"]
# Ground-truth model used to synthesize samples (cool): dT/dt = k*(Text-T) + u_fan
TRUE_K = 0.05
TRUE_U = {"low": -0.10, "med": -0.40, "high": -1.00}


def _seed_envelope(learning: ThermalLearning, *, noise: float = 0.0, n: int = 60) -> None:
    """Feed synthetic (outdoor_gap, slope) samples generated from the true model."""
    rng = random.Random(1234)
    for _ in range(n):
        for fan in FAN_MODES:
            gap = rng.uniform(-2.0, 12.0)  # spread of T_ext - T
            slope = TRUE_K * gap + TRUE_U[fan] + rng.uniform(-noise, noise)
            learning.add_envelope_sample(fan, gap, slope, "cool")


def test_envelope_dormant_without_samples() -> None:
    """Every envelope accessor returns None until samples are collected."""
    learning = ThermalLearning()
    assert learning.get_envelope_conductance("cool") is None
    assert learning.get_mode_cooling_power("low", "cool") is None
    assert learning.envelope_predicted_slope("low", "cool", 8.0) is None


def test_envelope_recovers_conductance_and_power() -> None:
    """The fixed-effects fit recovers k_env and each u_fan from clean samples."""
    learning = ThermalLearning()
    _seed_envelope(learning)
    assert learning.get_envelope_conductance("cool") == __import__("pytest").approx(TRUE_K, abs=0.01)
    for fan in FAN_MODES:
        assert learning.get_mode_cooling_power(fan, "cool") == __import__("pytest").approx(TRUE_U[fan], abs=0.03)


def test_envelope_needs_gap_spread() -> None:
    """With all samples at (nearly) the same outdoor gap, k_env is not identified."""
    learning = ThermalLearning()
    for _ in range(80):
        for fan in FAN_MODES:
            learning.add_envelope_sample(fan, 5.0, TRUE_K * 5.0 + TRUE_U[fan], "cool")
    assert learning.get_envelope_conductance("cool") is None


def test_envelope_predicted_slope_matches_model() -> None:
    """envelope_predicted_slope returns k_env·gap + u_fan."""
    learning = ThermalLearning()
    _seed_envelope(learning)
    k = learning.get_envelope_conductance("cool")
    u = learning.get_mode_cooling_power("high", "cool")
    assert learning.envelope_predicted_slope("high", "cool", 8.0) == __import__("pytest").approx(k * 8.0 + u, abs=1e-6)


def test_envelope_survives_serialization() -> None:
    """Envelope samples round-trip through to_dict/from_dict."""
    learning = ThermalLearning()
    _seed_envelope(learning)
    k_before = learning.get_envelope_conductance("cool")
    restored = ThermalLearning.from_dict(learning.to_dict())
    assert restored.envelope_sample_count() == learning.envelope_sample_count()
    assert restored.get_envelope_conductance("cool") == __import__("pytest").approx(k_before, abs=1e-9)


def test_from_dict_without_envelope_key_is_backward_compatible() -> None:
    """Restoring a pre-feature payload (no envelope_samples) yields an empty buffer."""
    restored = ThermalLearning.from_dict({"slope_samples": [], "response_events": []})
    assert restored.envelope_sample_count() == 0
    assert restored.get_envelope_conductance("cool") is None


def _mpc_with_envelope() -> MPCController:
    """MPC seeded with both projection slopes and the envelope model.

    The per-fan effective slopes make every mode look able to cool near
    equilibrium (so the cost model prefers stepping down to the cheapest fan);
    the envelope model decides whether that fan can actually hold at the current
    outdoor temperature.
    """
    learning = ThermalLearning()
    learning.set_mode_effective_slope("low", "cool", 0.2)
    learning.set_mode_effective_slope("med", "cool", 0.5)
    learning.set_mode_effective_slope("high", "cool", 1.0)
    _seed_envelope(learning)
    return MPCController(learning=learning, deadband=0.2, min_interval=10, fan_modes=FAN_MODES)


def test_feasibility_gate_blocks_infeasible_stepdown_when_hot() -> None:
    """When hot outside, a cost-preferred step-down to a fan that can't hold is blocked.

    Near the setpoint the cost model prefers the cheapest fan (low). But at
    T_ext − setpoint = 12 °C the model predicts low = 0.05·12 − 0.10 = +0.50 °C/h
    and med = +0.20 (both warm the room), so only high can hold — the gate must
    keep the controller off low/med.
    """
    mpc = _mpc_with_envelope()
    decision = mpc.evaluate(
        current_temp=24.0,  # at setpoint: the cost model wants to step down
        target_temp=24.0,
        vtherm_slope=0.1,
        hvac_mode="cool",
        current_fan="high",
        minutes_since_change=60.0,
        outdoor_temp=36.0,  # gap at setpoint = 12
    )
    assert decision["mpc_fan_mode"] not in ("low", "med")
    assert "Blocked" in decision["mpc_reason"]


def test_feasibility_gate_allows_stepdown_when_mild() -> None:
    """When it is mild outside, the cost-preferred weaker fan is allowed through."""
    mpc = _mpc_with_envelope()
    # gap at setpoint = 1 => med predicts 0.05*1 - 0.40 = -0.35 (cools/holds), so
    # the cost-preferred step-down away from high is allowed.
    decision = mpc.evaluate(
        current_temp=24.0,
        target_temp=24.0,
        vtherm_slope=0.1,
        hvac_mode="cool",
        current_fan="high",
        minutes_since_change=60.0,
        outdoor_temp=25.0,  # gap at setpoint = 1
    )
    assert decision["mpc_would_change_now"] == "yes"
    assert decision["mpc_fan_mode"] in ("low", "med")
    assert "Blocked" not in decision["mpc_reason"]


def test_envelope_projection_uses_grey_box_model_when_enabled() -> None:
    """With USE_ENVELOPE_PROJECTION on, projections follow k_env·(Text-T)+u_fan.

    The gap-model and envelope projections should diverge for the same state,
    confirming the envelope path is actually driving the simulation. Off by
    default, so this flips the module flag explicitly.
    """
    learning = ThermalLearning()
    learning.set_mode_effective_slope("low", "cool", 0.2)
    learning.set_mode_effective_slope("med", "cool", 0.5)
    learning.set_mode_effective_slope("high", "cool", 1.0)
    _seed_envelope(learning)
    mpc = MPCController(learning=learning, deadband=0.2, min_interval=10, fan_modes=FAN_MODES)

    kwargs = dict(
        current_temp=26.0,
        target_temp=24.0,
        vtherm_slope=-1.0,
        hvac_mode="cool",
        current_fan="high",
        minutes_since_change=60.0,
        outdoor_temp=34.0,
    )
    original = mpc_module.USE_ENVELOPE_PROJECTION
    try:
        mpc_module.USE_ENVELOPE_PROJECTION = False
        gap = mpc.evaluate(**kwargs)
        mpc_module.USE_ENVELOPE_PROJECTION = True
        env = mpc.evaluate(**kwargs)
    finally:
        mpc_module.USE_ENVELOPE_PROJECTION = original

    # Same far-from-target state, but the two projection models give different
    # 30-min temperature forecasts.
    assert gap["mpc_predicted_temperature_30m"] != env["mpc_predicted_temperature_30m"]


def test_envelope_projection_dormant_without_outdoor() -> None:
    """Even with the flag on, no outdoor temp means the gap model is used."""
    learning = ThermalLearning()
    _seed_envelope(learning)
    mpc = MPCController(learning=learning, deadband=0.2, min_interval=10, fan_modes=FAN_MODES)
    original = mpc_module.USE_ENVELOPE_PROJECTION
    try:
        mpc_module.USE_ENVELOPE_PROJECTION = True
        assert mpc._envelope_params("low", "cool", None) is None
    finally:
        mpc_module.USE_ENVELOPE_PROJECTION = original


def test_no_outdoor_temp_leaves_envelope_note_absent() -> None:
    """Without outdoor_temp the envelope gate is dormant (behaviour unchanged)."""
    mpc = _mpc_with_envelope()
    decision = mpc.evaluate(
        current_temp=24.0,
        target_temp=24.0,
        vtherm_slope=0.1,
        hvac_mode="cool",
        current_fan="high",
        minutes_since_change=60.0,
    )
    # No outdoor sensor: the envelope feasibility note must never appear.
    assert "can't hold the setpoint" not in decision["mpc_reason"]
