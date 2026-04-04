---
name: modify-mpc-shadow
description: "Modify the MPC shadow controller: cost function tuning, hysteresis thresholds, simulation model, disturbance tracking, or adding new pausing conditions. Use when: changing how the shadow evaluates or recommends fan modes; adjusting cost weights or switch-gain margins; adding new disturbance sources; modifying the step-down hold logic; changing how confidence is computed."
argument-hint: "Description of the MPC shadow behavior to change (e.g. 'reduce overshoot penalty weight')"
---

# Modify MPC Shadow Controller

## Architecture

The MPC shadow lives in `mpc_shadow.py` as `MPCShadowController`. It is **read-only**: it never calls HA services or modifies the live controller. It runs every control cycle (~2 min) and produces a recommendation dict that feeds sensors and CSV logs.

### Key Flow in `evaluate()`

```
1. Early exits: disabled, idle HVAC mode
2. Resolve fan modes and active fan
3. Compute effective slope, dead time, phase
4. Update disturbance bias (EMA tracking)
5. Pause conditions: window-open, defrost → return "Disturbed"
6. Setpoint drop → return lowest mode immediately
7. Simulate ALL fan modes over the horizon (30 min default)
8. Select best by lowest cost
9. Apply guards: min-interval hold, hysteresis, step-down hold, step-down limit
10. Build and return payload
```

### Cost Function (`_simulate_mode`)

Each candidate fan mode is simulated step-by-step over the horizon:

| Cost Component | Weight | Purpose |
|---|---|---|
| `comfort_error × urgency` | 1.0 × (1 + excess error × 2) | Penalizes being outside deadband |
| `overshoot²` | 3.0 | Strongly penalizes going past target |
| `floor_violation` (linear) | 12.0 × urgency | Penalizes being below target (heat) |
| `floor_violation²` | 30.0 | Strongly penalizes large shortfalls |
| `mode_change_cost` | 0.15 × distance | Penalizes switching fan modes |
| `mode_rank_cost` | 0.05 × (rank+1) | Slight preference for lower fan speeds |
| `min_interval_penalty` | 25.0 | Blocks changes before min_interval |

### Hysteresis (`_required_switch_gain`)

The shadow requires a minimum cost improvement before switching:

| Situation | Base Margin |
|---|---|
| Over-target or far under | 0.10 |
| Approaching target | 0.15 |
| Near target (within deadband) | 0.30 |

Plus bonuses for non-established phase (+0.10) and step distance (+0.05/step).

### Step-Down Guards

- `_step_down_hold_note()`: blocks downward moves when still under target and either not established or predicted shortfall at 10 min
- `_apply_step_down_limit()`: enforces ±1 step per cycle for downward moves (upward is unrestricted)

### Disturbance Bias (`_update_disturbance_bias`)

Tracks slow external perturbations (solar, occupancy). Only updates during ESTABLISHED phase with a known profile. Decays during window-open or defrost. Clamped to ±2.0 °C/h.

### Pause Conditions

The shadow pauses (returns "Disturbed") during:
- **Window open**: thermal model unreliable
- **Defrost active**: slope data is corrupted by heat-pump defrost cycle

Both conditions decay the disturbance bias without updating it.

## Procedure

### 1. Identify the Change Area

| Change | File/Method |
|---|---|
| Cost weights | `_simulate_mode()` constants at module top |
| Hysteresis margins | `_required_switch_gain()` constants at module top |
| Step-down guard | `_step_down_hold_note()` |
| New pause condition | `evaluate()` after disturbance update, before setpoint-drop check |
| Disturbance tracking | `_update_disturbance_bias()` |
| Simulation model | `_simulate_mode()` inner loop |
| Confidence | `_compute_confidence()` |

### 2. Make the Change

Constants are at the module level (e.g., `FLOOR_VIOLATION_LINEAR_WEIGHT = 12.0`). If a new constant is user-tunable, add it to `const.py` with `DEFAULT_` prefix and expose in `config_flow.py`.

Key constraints:
- **Never call HA services** from the shadow
- **Never modify controller state** — the shadow is observation-only
- **Respect the payload format** — all keys must start with `mpc_shadow_`
- **Log at DEBUG level** — shadow logs are verbose by design

### 3. Write Tests

Tests go in `tests/test_mpc_shadow.py`. Use the existing helpers:

```python
def test_shadow_<behavior>() -> None:
    """<What this tests>."""
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
        current_temp=..., target_temp=...,
        vtherm_slope=..., hvac_mode="heat",
        current_fan="medium", live_decision_fan="medium",
        is_window_open=False, minutes_since_change=20.0,
    )

    assert result["mpc_shadow_fan_mode"] == ...
    assert result["mpc_shadow_status"] == ...
```

### 4. Update Documentation

- **`README.md`** — MPC Shadow section if user-visible behavior changes
- **`docs/mpc_shadow_mode.md`** — technical design document
- **`.github/copilot-instructions.md`** — if adding new constraints

### 5. Validate

```bash
python -m pytest tests/test_mpc_shadow.py -q   # shadow tests
python -m pytest tests/ -q                      # all tests
```
