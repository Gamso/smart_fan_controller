# MPC Controller — Shadow Observation and Production Mode

## Purpose

The MPC controller runs alongside the rule-based heuristic every cycle.
It maintains a learned thermal model, scores every candidate fan mode, and produces a recommendation.

The integration offers two operating modes:

- **MPC observation mode** (default): the MPC recommendation is logged and exposed as diagnostics only.
  The rule-based heuristic remains solely responsible for sending `set_fan_mode` commands.
  This is the low-risk path to validate the predictive controller before activating it.

- **Production mode**: when enabled via `switch.smart_fan_controller_mpc_production_mode`, the MPC
  recommendation overrides the heuristic fan decision for every cycle where the MPC controller
  produces a confident, actionable result.  The heuristic decision is still computed and logged for
  comparison, but the real `set_fan_mode` command uses the MPC-selected mode.

## Goals

- Learn the thermal behavior of a specific room with minimal manual tuning.
- Reuse the data already collected by the integration.
- Compare heuristic decisions and predictive decisions side by side.
- Keep the controller explainable and easy to debug from Home Assistant diagnostics.

## Non-Goals

- No reinforcement learning or opaque black-box model is introduced.
- No second-order slope terms: VTherm slope is already EMA-smoothed, so the model avoids parabolic
  projection that would amplify noise.

## Runtime Architecture

Each control cycle follows this flow:

1. Read VTherm temperature, target, slope, HVAC mode, and current fan mode.
2. Run the existing heuristic controller and record its decision.
3. Run `MPCController.evaluate(...)` with the same inputs.
4. Merge the MPC diagnostics into the sensor payload.
5. Append both heuristic and MPC information to the CSV log.
6. **MPC observation mode**: apply the heuristic fan change, if any.
   **Production mode**: apply the MPC fan recommendation when the MPC status is `Ready` or
   `Setpoint drop`; fall back to the heuristic otherwise (e.g. status `Low confidence`,
   `Disturbed`, `Disabled`, `Idle`).

### HA Entities

| Platform | Entity ID | Purpose |
|----------|-----------|---------|
| `switch` | `switch.smart_fan_controller_mpc_production_mode` | Enable/disable MPC production override |
| `sensor` | `sensor.smart_fan_controller_mpc_status` | Current MPC recommendation status |
| `sensor` | `sensor.smart_fan_controller_mpc_reason` | Human-readable reason for the recommendation |
| `sensor` | `sensor.smart_fan_controller_mpc_fan_mode` | Recommended fan mode |
| `sensor` | `sensor.smart_fan_controller_mpc_match` | Whether MPC and heuristic agree |
| `sensor` | `sensor.smart_fan_controller_mpc_would_change_now` | Whether MPC would change the fan this cycle |
| `sensor` | `sensor.smart_fan_controller_mpc_cost` | Best cost score for the recommended mode |
| `sensor` | `sensor.smart_fan_controller_mpc_confidence` | Confidence percentage |
| `sensor` | `sensor.smart_fan_controller_mpc_predicted_temperature_10_min` | 10-minute temperature forecast |
| `sensor` | `sensor.smart_fan_controller_mpc_predicted_temperature_30_min` | 30-minute temperature forecast |
| `sensor` | `sensor.smart_fan_controller_mpc_dead_time` | Dead time used by the simulator (min) |
| `sensor` | `sensor.smart_fan_controller_mpc_known_profiles` | Number of reliable learned profiles |
| `sensor` | `sensor.smart_fan_controller_mpc_disturbance_bias` | Slow disturbance correction term |
| `sensor` | `sensor.smart_fan_controller_mpc_heat_profiles` | Per-mode learned profiles for heat |
| `sensor` | `sensor.smart_fan_controller_mpc_cool_profiles` | Per-mode learned profiles for cool |

CSV log fields are prefixed with `mpc_*`.

Even while it shares the same learning backend today, the MPC controller owns its own runtime parameters (`deadband`, `min_interval`, `fan_modes`) and no longer depends on protected members of the live heuristic controller.

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

If a reliable profile is not yet available for a fan mode, the MPC model falls back to a coarse rank-based estimate derived from the current slope. Confidence is lowered accordingly.

### Disturbance Handling

When the current fan mode has a reliable learned profile and the controller is in `ESTABLISHED` phase, the MPC model estimates a slow disturbance bias from the residual:

```text
residual = observed_effective_power - expected_effective_power
bias[k+1] = (1 - beta) * bias[k] + beta * residual
```

This helps the MPC model remain usable when the room is slightly helped or hindered by effects not directly modeled by the fan itself.

When a window is detected as open, the MPC model does not trust its own predictions and switches to a `Disturbed` status instead of producing an actionable recommendation.

## MPC-lite Decision Rule

The controller simulates every available fan mode on a short fixed horizon of 30 minutes.
The current scaffold keeps the action constant over the horizon to stay simple and fast.
The simulator supports both `heat` and `cool`; cooling uses the same learned effective-power model with the sign inverted back to room-temperature evolution.

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
- `min_interval_penalty` keeps the MPC recommendation aligned with the same actuator guardrail as the live controller

In addition, the current implementation applies a mode-independent floor penalty when the predicted room temperature drops below the setpoint. This reflects a conservative comfort rule: if the target is `20°C`, predictions below `20°C` are considered increasingly unacceptable in both `heat` and `cool`.

The selected mode is the one with the lowest total cost.
To avoid fan yo-yo near the setpoint, a recommendation that changes the fan must also beat the current mode by a minimum gain. If the gain is only marginal, the MPC controller keeps the current fan and reports that hysteresis blocked the switch.

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

The CSV log also stores the MPC recommendation so we can replay and compare decisions offline.

## Validation Strategy

The MPC controller should stay in observation mode until it meets clear quality gates.
Recommended promotion criteria:

- stable behavior over at least 7 days
- lower or equal overshoot than the heuristic controller
- no increase in fan oscillations
- acceptable prediction quality on live data
- confidence high enough on the modes frequently used by the room

Suggested metrics to track:

- MAE at 10 minutes
- MAE at 30 minutes
- percentage of cycles where MPC and heuristic agree
- number of hypothetical fan changes per day
- overshoot duration above target

## Implementation Map

Current files involved:

- `custom_components/smart_fan_controller/mpc_controller.py`: MPC thermal model and MPC-lite scorer
- `custom_components/smart_fan_controller/controller.py`: filters disturbed response-time learning at the source
- `custom_components/smart_fan_controller/__init__.py`: runtime coexistence and sensor payload merge
- `custom_components/smart_fan_controller/sensor.py`: MPC diagnostics exposed in Home Assistant
- `custom_components/smart_fan_controller/switch.py`: switch to enable or disable MPC production mode (`switch.smart_fan_controller_mpc_production_mode`)
- `custom_components/smart_fan_controller/data_collection.py`: MPC fields appended to the CSV log

## Next Steps

After enough MPC validation and before enabling production mode:

1. improve the thermal model with residual correction and better disturbance handling
2. add offline replay tooling against recorded CSV traces
3. verify MPC metrics are consistently good before enabling `switch.smart_fan_controller_mpc_production_mode`
