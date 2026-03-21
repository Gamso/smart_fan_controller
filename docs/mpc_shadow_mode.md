# MPC Shadow Mode

## Purpose

MPC Shadow Mode lets the current rule-based controller and a learned thermal model coexist safely.
The existing heuristic controller remains the only component allowed to send real `set_fan_mode` commands.
The shadow controller runs every cycle, computes what it would do, and only exposes diagnostics and CSV logs.

This gives us a low-risk path to validate a predictive controller against real usage before activating it.

## Goals

- Learn the thermal behavior of a specific room with minimal manual tuning.
- Reuse the data already collected by the integration.
- Compare heuristic decisions and predictive decisions side by side.
- Keep the controller explainable and easy to debug from Home Assistant diagnostics.

## Non-Goals

- No live fan command is sent by the shadow controller.
- No reinforcement learning or opaque black-box model is introduced in this first stage.
- No change to the current safety ownership: the heuristic controller stays authoritative.

## Runtime Architecture

Each control cycle now follows this flow:

1. Read VTherm temperature, target, slope, HVAC mode, and current fan mode.
2. Run the existing heuristic controller and keep its decision as the live action.
3. Run `MPCShadowController.evaluate(...)` with the same inputs.
4. Merge the shadow diagnostics into the sensor payload.
5. Append both heuristic and shadow information to the CSV log.
6. Apply only the heuristic fan change, if any.

The shadow controller is available through:

- `switch.smart_fan_mpc_shadow_mode`
- diagnostic sensors prefixed with `sensor.smart_fan_mpc_shadow_*`
- CSV fields prefixed with `mpc_shadow_*`

Even while it shares the same learning backend today, the shadow controller owns its own runtime parameters (`deadband`, `min_interval`, `fan_modes`) and no longer depends on protected members of the live heuristic controller.

## Learned Thermal Model

The current version uses a temperature-state model driven by learned fan-mode gains, a learned dead time, and a disturbance correction term.
It still consumes `temperature_slope`, but the slope is treated as an observation that helps estimate thermal power, not as the final state to optimize directly.

Definitions:

- `T_hat[k]`: predicted room temperature at step `k`
- `q_hat[k]`: predicted effective thermal power at step `k` (expressed in equivalent `°C/h`)
- `u`: candidate fan mode held constant over the horizon
- `dead_time`: learned thermal delay between a fan change and a visible sensor response
- `bias`: slow disturbance correction term for unmodeled effects such as solar gain or occupancy

For each candidate mode:

```text
if elapsed <= dead_time:
    target_power = current_effective_power
else:
    target_power = learned_mode_power(candidate_mode) + bias

q_hat[k+1] = q_hat[k] + alpha * (target_power - q_hat[k])
T_hat[k+1] = T_hat[k] + delta_t_hours * raw_slope(q_hat[k+1], hvac_mode)
```

Where:

- `alpha = 0.35` in the current scaffold
- `delta_t = 2 min`
- `raw_slope = +q_hat` in heating and `-q_hat` in cooling

### Source of Learned Parameters

The model reuses the current learning subsystem:

- `learning.get_dead_time()` for thermal delay
- `learning.get_mode_effective_slope(fan_mode, hvac_mode)` for reliable per-mode profiles

If a reliable profile is not yet available for a fan mode, the shadow model falls back to a coarse rank-based estimate derived from the current slope. Confidence is lowered accordingly.

### Disturbance Handling

When the current fan mode has a reliable learned profile and the controller is in `ESTABLISHED` phase, the shadow model estimates a slow disturbance bias from the residual:

```text
residual = observed_effective_power - expected_effective_power
bias[k+1] = (1 - beta) * bias[k] + beta * residual
```

This helps the shadow model remain usable when the room is slightly helped or hindered by effects not directly modeled by the fan itself.

When a window is detected as open, the shadow model does not trust its own predictions and switches to a `Disturbed` status instead of producing an actionable recommendation.

## MPC-lite Decision Rule

The controller simulates every available fan mode on a short fixed horizon of 30 minutes.
The current scaffold keeps the action constant over the horizon to stay simple and fast.

Each candidate mode gets a scalar cost:

```text
J(mode) =
    sum(comfort_error)
  + 4 * sum(overshoot^2)
  + 0.4 * fan_step_distance
  + 0.15 * fan_energy_rank
  + min_interval_penalty
```

Where:

- `comfort_error = max(abs(error) - deadband, 0)`
- `overshoot = max(-error, 0)`
- `fan_step_distance` penalizes unnecessary fan jumps
- `fan_energy_rank` lightly discourages staying on the highest modes all the time
- `min_interval_penalty` keeps the shadow recommendation aligned with the same actuator guardrail as the live controller

The selected mode is the one with the lowest total cost.

## Shadow Diagnostics

The current implementation exposes:

- recommendation status
- recommendation reason
- recommended fan mode
- whether it matches the live heuristic decision
- whether it would actually change the fan now
- 10-minute and 30-minute predicted temperatures
- confidence percentage
- dead time used by the simulator
- number of reliable learned profiles available
- current disturbance bias estimate

The CSV log also stores the shadow recommendation so we can replay and compare decisions offline.

## Validation Strategy

The shadow controller should stay in observation mode until it meets clear quality gates.
Recommended promotion criteria:

- stable behavior over at least 7 days
- lower or equal overshoot than the heuristic controller
- no increase in fan oscillations
- acceptable prediction quality on live data
- confidence high enough on the modes frequently used by the room

Suggested metrics to track:

- MAE at 10 minutes
- MAE at 30 minutes
- percentage of cycles where shadow and heuristic agree
- number of hypothetical fan changes per day
- overshoot duration above target

## Implementation Map

Current files involved:

- `custom_components/smart_fan_controller/mpc_shadow.py`: shadow thermal model and MPC-lite scorer
- `custom_components/smart_fan_controller/controller.py`: filters disturbed response-time learning at the source
- `custom_components/smart_fan_controller/__init__.py`: runtime coexistence and sensor payload merge
- `custom_components/smart_fan_controller/sensor.py`: shadow diagnostics exposed in Home Assistant
- `custom_components/smart_fan_controller/switch.py`: switch to enable or disable shadow mode
- `custom_components/smart_fan_controller/data_collection.py`: shadow fields appended to the CSV log

## Next Steps

After enough shadow validation, the recommended evolution is:

1. improve the thermal model with residual correction and better disturbance handling
2. add offline replay tooling against recorded CSV traces
3. introduce a separate `mpc_active` mode only after shadow metrics are consistently good
