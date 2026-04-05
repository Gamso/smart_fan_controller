# Smart Fan Controller — Home Assistant Custom Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Predictive fan speed control for HVAC systems, designed to work with Versatile Thermostat.

---

## Table of Contents

- [Smart Fan Controller — Home Assistant Custom Integration](#smart-fan-controller--home-assistant-custom-integration)
  - [Table of Contents](#table-of-contents)
  - [Installation](#installation)
    - [HACS (Recommended)](#hacs-recommended)
    - [Manual Installation](#manual-installation)
  - [Overview](#overview)
    - [How It Works](#how-it-works)
  - [Requirements](#requirements)
  - [Quick Setup](#quick-setup)
  - [Configuration Parameters](#configuration-parameters)
  - [Control Logic](#control-logic)
    - [Decision Priority (Zones A–F)](#decision-priority-zones-af)
    - [Temperature Projection](#temperature-projection)
    - [Phase Detection](#phase-detection)
    - [Safety Constraints](#safety-constraints)
    - [Defrost Detection](#defrost-detection)
    - [HVAC Idle Detection](#hvac-idle-detection)
  - [Learning System](#learning-system)
    - [Per-Mode Fan Profiles](#per-mode-fan-profiles)
    - [Dead Time Calibration](#dead-time-calibration)
    - [Window-Open Filtering](#window-open-filtering)
    - [Defrost Learning Exclusion](#defrost-learning-exclusion)
    - [HVAC Idle Learning Exclusion](#hvac-idle-learning-exclusion)
  - [Sensors \& Entities](#sensors--entities)
    - [Main Entities](#main-entities)
    - [Diagnostic Sensors](#diagnostic-sensors)
    - [Defrost Diagnostic](#defrost-diagnostic)
  - [Services](#services)
    - [`smart_fan_controller.apply_learned_settings`](#smart_fan_controllerapply_learned_settings)
    - [`smart_fan_controller.reset_learning`](#smart_fan_controllerreset_learning)
  - [Troubleshooting](#troubleshooting)
  - [License](#license)

---

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations**
3. Click the three-dot menu → **Custom repositories**
4. Add `https://github.com/Gamso/smart_fan_controller` with category **Integration**
5. Search for **Smart Fan Controller** and install it
6. Restart Home Assistant

Alternatively, click the button below to open this repository directly in HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gamso&repository=smart_fan_controller&category=integration)

### Manual Installation

1. Copy the `custom_components/smart_fan_controller` directory to your Home Assistant `custom_components` folder
2. Restart Home Assistant
3. Add the integration via the UI (Settings → Devices & Services → Add Integration)

---

## Overview

Smart Fan Controller is a custom Home Assistant integration that **adjusts HVAC fan speed** based on how the temperature is evolving, not just the current reading. It uses a rule-based decision engine with six priority zones, phase-aware timing, and an automatic learning system.

### How It Works

1. Every 2 minutes, reads the current temperature, target, and temperature slope from Versatile Thermostat
2. Projects temperature **10 minutes ahead** using a clamped linear model
3. Classifies the thermal phase (dead time / transient / established) relative to the last fan change
4. Evaluates six priority zones (A–F) and selects the first matching action
5. Applies safety guards (step-down limit, min interval) and changes fan speed if needed
6. Collects learning data to automatically calibrate parameters over time
7. Optionally runs an MPC shadow controller in the background for dry-run comparison

> Experimental: **MPC Shadow Mode** runs a learned temperature-state model and MPC-lite in observation-only mode. It owns its own runtime parameters (`deadband`, `min_interval`, fan modes), never applies real fan commands, pauses itself during disturbed periods such as an open window, and keeps those periods out of dead-time learning. See [docs/mpc_shadow_mode.md](docs/mpc_shadow_mode.md).
> It supports both `heat` and `cool`, and includes a hysteresis guard so tiny cost differences do not create fan yo-yo around the setpoint.

---

## Requirements

- A **climate entity** with multiple fan speeds (e.g., `low`, `medium`, `high`)
- **Versatile Thermostat** (or compatible integration) that exposes `temperature_slope` in its `specific_states` attribute

---

## Quick Setup

1. Go to **Settings → Devices & Services → Add Integration → Smart Fan Controller**
2. Select your climate entity
3. Configure parameters (or use defaults)
4. Save — the controller starts working immediately

---

## Configuration Parameters

All parameters can be changed at any time via **Settings → Devices & Services → Smart Fan Controller → Configure**.

| Parameter            | Default  | Range            | Description                                                                                                                                                                                                   |
| -------------------- | -------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Deadband**         | `0.2°C`  | `0.0` – `5.0°C`  | Comfort zone around target — no action taken within this range. Increase to reduce fan changes.                                                                                                               |
| **Min Interval**     | `10 min` | `1` – `60 min`   | Minimum time between non-emergency fan changes. Prevents rapid oscillations.                                                                                                                                  |
| **Soft Error**       | `0.3°C`  | `0.0` – `10.0°C` | Error threshold that triggers recovery mode. Should be larger than deadband.                                                                                                                                  |
| **Hard Error**       | `0.6°C`  | `0.0` – `10.0°C` | Error threshold that triggers emergency mode (max fan, bypasses min interval).                                                                                                                                |
| **Limit Timeout**    | `15 min` | `10` – `120 min` | Maximum time before forcing a re-evaluation, even without significant slope change.                                                                                                                           |
| **Learning Enabled** | `true`   | —                | Enables the automatic learning system. Disable for fully manual tuning.                                                                                                                                       |
| **MPC Shadow Mode**  | `false`  | —                | Runs the learned model + MPC-lite in the background for comparison only. No real fan command is applied.                                                                                                      |
| **Defrost Entity**   | *(none)* | —                | Optional entity (`binary_sensor`, `sensor`, or `input_boolean`) that reports when the heat pump is in defrost cycle. When active, defrost protection is applied. See [Defrost Detection](#defrost-detection). |

> **Tip — recommended ratios**: `deadband < soft_error < hard_error`, e.g. `0.2 / 0.3 / 0.6`.

---

## Control Logic

Each cycle the controller computes the temperature error (always positive when the system needs more action) and applies the first matching rule from six priority zones:

### Decision Priority (Zones A–F)

| Zone      | Condition                                          | Action                                                                       |
| --------- | -------------------------------------------------- | ---------------------------------------------------------------------------- |
| **A**     | `error ≥ hard_error`                               | **Emergency** — max fan, bypasses timer                                      |
| **A-bis** | Target dropped by more than 1°C                    | **Setpoint drop** — min fan, bypasses timer                                  |
| **B**     | Projected overshoot `> deadband` AND slope changed | **Braking** — decrease fan proactively                                       |
| **C**     | `error > soft_error`                               | **Recovery** — increase fan (or patience during dead time / improving slope) |
| **D**     | `0 < error < soft_error`                           | **Drift / Descent** — adjust towards target (phase-gated)                    |
| **E**     | `error < -deadband`                                | **Over-target** — decrease fan                                               |
| **F**     | `-deadband ≤ error ≤ 0`                            | **Comfort zone** — hold (react to slow drift)                                |

> **Note**: In heat mode, error = `target - current`. In cool mode, error = `current - target`. A positive value always means the system needs more action.

**Zone C details**: During the dead-time phase, the controller reports "Patience: Waiting for thermal response" instead of boosting, to avoid over-reacting before the sensor can reflect the last change. After dead time, if the slope hasn't improved, it increases fan speed.

**Zone D details**: Four sub-rules gated by control phase:
- **Descent**: Strong favorable slope in ESTABLISHED phase, already within deadband, and projection at/past target → reduce fan
- **Hold**: Strong favorable slope but still too far from target → keep current fan
- **Drift**: Slow drift away from target → increase fan
- **Proactive**: Stable but away from target in ESTABLISHED phase → increase fan to reach setpoint

### Temperature Projection

The controller projects temperature 10 minutes ahead using a **clamped linear model**:

```
projected_temperature = current_temp + (vtherm_slope × 10/60)
```

The projection is clamped to ±1.0°C from the current temperature to prevent extreme predictions from noisy slope readings. This value was calibrated against real sensor data showing maximum overshoot of ~0.6°C.

> The VTherm slope is already an EMA-smoothed signal over ~15–30 minutes, so a second-order (parabolic) term would amplify noise from the double derivative of an already-filtered signal. The clamped linear model is simpler and more robust.

### Phase Detection

After each fan speed change, the controller classifies the elapsed time into three phases:

| Phase           | Condition                             | Meaning                           |
| --------------- | ------------------------------------- | --------------------------------- |
| **DEAD_TIME**   | `elapsed < dead_time`                 | Sensor hasn't reacted yet         |
| **TRANSIENT**   | `dead_time ≤ elapsed < dead_time×1.5` | Sensor starting to respond        |
| **ESTABLISHED** | `elapsed ≥ dead_time × 1.5`           | Slope reflects current fan regime |

The default dead time is 10 minutes. When the learning system is ready, it is replaced by the learned median response time (typically 3–5 minutes for well-placed sensors).

### Safety Constraints

- **Step-down protection**: Fan speed can only decrease by one step at a time (e.g., `turbo → high`), preventing abrupt pressure changes and protecting the motor.
- **Min interval**: Non-emergency changes respect the effective timeout. Emergency (Zone A) and setpoint drop (Zone A-bis) override it.

### Defrost Detection

When a heat pump defrosts its outdoor coil, the heat output drops sharply — the HVAC is working to melt ice, not heat the room. Without defrost awareness, the controller would misinterpret the falling temperature slope as insufficient fan speed and try to increase it, or worse, would reduce the fan thinking the slope is already favorable when in fact the defrost is about to end.

**External entity (optional)**: Configure a `binary_sensor`, `sensor`, or `input_boolean` from your PAC integration that reports defrost state. When this entity is `on`/`true`/`1`, defrost protection is activated and the 20-minute cooldown timer is refreshed every control cycle — protection stays active *as long as the entity reports defrost*, then the cooldown applies after it clears.

**During defrost protection**:
- **Zones B and D are blocked**: no step-down decisions (braking or favorable-slope reduction)
- The decision reason appears as `"Defrost hold: …"`
- **Learning samples are excluded**: slope data during defrost is not added to per-mode profiles, preventing corrupted calibration

### HVAC Idle Detection

When the heat pump compressor is off (setpoint reached, system coasting) the HVAC is not actively heating or cooling and the fan should not be increased in response to a small temperature drift.

**Operating entity (optional)**: Configure a `binary_sensor`, `sensor`, or `input_boolean` from your PAC integration that reports whether the compressor is running. When this entity is `off`/`false`/`0`, the compressor is considered idle.

> When no operating entity is configured, HVAC idle detection is disabled.

**During HVAC idle**:
- **Zones C and D are held**: no step-up decisions while the compressor is off
- The decision reason appears as `"HVAC idle: compressor off, holding current speed"`
- **Zones A, A-bis, B, and E are unaffected**: emergency, setpoint-drop, braking, and step-down decisions still operate normally
- **Learning samples are excluded**: slope and response-time data while idle is not added to profiles (see [HVAC Idle Learning Exclusion](#hvac-idle-learning-exclusion))

---

## Learning System

The integration includes an **automatic learning system** that collects data during normal operation and computes optimal parameters after approximately 48–72 hours (≥240 samples, collected every 2 minutes).

**Data collected**:
- Temperature slope and active fan mode (every 2 minutes)
- Time from fan speed change to next significant slope change (thermal response time)
- HVAC mode (heat/cool) for per-mode profiling

**Parameters computed from data**:

| Parameter       | Formula                                           |
| --------------- | ------------------------------------------------- |
| `deadband`      | `0.15 + (volatility_factor × 0.2)`                |
| `soft_error`    | `0.25 + (volatility_factor × 0.3)`                |
| `hard_error`    | `0.5 + (volatility_factor × 0.4)`                 |
| `limit_timeout` | rounded median of measured thermal response times |

Where `volatility_factor = min(slope_stdev / slope_mean, 3.0)`.

> **Important**: the learned `limit_timeout` is the stored base response estimate. At runtime, non-emergency decisions use `effective_timeout = max(min_interval, dead_time × 1.5)` once learning is ready.

Once learning is ready, parameters are **automatically applied** and the integration reloads. To apply manually, use the `apply_learned_settings` service. To start over, use `reset_learning`.

**Control**: Enable or disable learning at any time via `switch.smart_fan_controller_learning_enabled`. Existing data is preserved when disabled.

### Per-Mode Fan Profiles

The learning system tracks the **effective slope per fan mode and HVAC mode** (e.g., "medium in heat" vs "high in cool"). This data provides visibility into which fan speeds are most effective for each mode.

Profiles require at least 10 samples per mode to be considered reliable. Samples collected during window-open periods or large setpoint drops (night mode) are automatically filtered out.

### Dead Time Calibration

The system measures the **thermal response time** — the delay between a fan speed change and the first observable slope change at the sensor. This median value replaces the default 10-minute dead time, allowing the controller to be patient during the actual thermal lag period and reactive once the effect materializes.

Response events are only recorded when the delay is between 2 and 60 minutes (filtering sensor noise and system-off periods).

### Window-Open Filtering

When Versatile Thermostat reports a window as open (via the `window_manager.window_state` attribute), the controller:
- **Continues making live heuristic fan decisions** normally (the HVAC system is still running)
- **Stops collecting learning data**, including both per-mode slope samples and response-time events used to learn `dead_time`
- **Pauses the MPC shadow model**, marking it as disturbed instead of trusting predictions during the perturbation

This prevents window-open periods from corrupting the learned profiles.

### Defrost Learning Exclusion

Slope samples and response-time events collected during an active defrost period (auto-detected or via external entity, including the 20-minute cooldown) are **not added to learned profiles**. Defrost distorts the effective slope per fan mode and would bias the learning system toward lower heating capacity estimates.

### HVAC Idle Learning Exclusion

Slope samples and response-time events collected while the compressor is detected as idle (via `operating_entity` or `power_entity`) are **not added to learned profiles**. When the compressor is off the measured slope reflects ambient drift rather than active heating or cooling capacity, and recording it would corrupt per-mode profiles and dead-time calibration.

---

## Sensors & Entities

### Main Entities

| Entity                                         | Type   | Description                                       |
| ---------------------------------------------- | ------ | ------------------------------------------------- |
| `sensor.smart_fan_controller_fan_mode`         | Sensor | Current fan mode selected by the controller       |
| `sensor.smart_fan_controller_status`           | Sensor | Current control zone and decision reason          |
| `switch.smart_fan_controller_learning_enabled` | Switch | Enable / disable the learning system              |
| `switch.smart_fan_controller_mpc_shadow_mode`  | Switch | Enable / disable observation-only MPC shadow mode |

### Diagnostic Sensors

| Entity                                                                | Unit  | Description                                                                |
| --------------------------------------------------------------------- | ----- | -------------------------------------------------------------------------- |
| `sensor.smart_fan_controller_temperature_error`                       | °C    | Current temperature error (positive = needs action)                        |
| `sensor.smart_fan_controller_temperature_projected_10_min`            | °C    | Predicted temperature 10 minutes ahead                                     |
| `sensor.smart_fan_controller_temperature_projected_error_10_min`      | °C    | Predicted error 10 minutes ahead                                           |
| `sensor.smart_fan_controller_fan_mode_last_change`                    | min   | Time elapsed since last fan mode change                                    |
| `sensor.smart_fan_controller_learning_progress`                       | %     | Learning completion (100% = ≥240 samples)                                  |
| `sensor.smart_fan_controller_learning_status`                         | —     | `"Learning (45%)"` or `"Ready"`                                            |
| `sensor.smart_fan_controller_learning_samples`                        | count | Number of slope samples collected                                          |
| `sensor.smart_fan_controller_learning_response_events`                | count | Number of thermal response time measurements                               |
| `sensor.smart_fan_controller_learned_dead_time`                       | min   | Median learned thermal response delay (`dead_time`)                        |
| `sensor.smart_fan_controller_effective_timeout`                       | min   | Actual non-emergency timeout currently used                                |
| `sensor.smart_fan_controller_learned_deadband`                        | °C    | Learned optimal deadband                                                   |
| `sensor.smart_fan_controller_learned_soft_error`                      | °C    | Learned optimal soft error threshold                                       |
| `sensor.smart_fan_controller_learned_hard_error`                      | °C    | Learned optimal hard error threshold                                       |
| `sensor.smart_fan_controller_learned_limit_timeout`                   | min   | Learned base timeout stored in config                                      |
| `sensor.smart_fan_controller_mpc_shadow_status`                       | —     | Shadow controller state (`Disabled`, `Ready`, etc.)                        |
| `sensor.smart_fan_controller_mpc_shadow_reason`                       | —     | Explanation of the current shadow recommendation                           |
| `sensor.smart_fan_controller_mpc_shadow_fan_mode`                     | —     | Fan mode the MPC shadow would choose                                       |
| `sensor.smart_fan_controller_mpc_shadow_match`                        | —     | Whether the shadow recommendation matches the live heuristic               |
| `sensor.smart_fan_controller_mpc_shadow_would_change_now`             | —     | Whether the shadow controller would actively change the fan right now      |
| `sensor.smart_fan_controller_mpc_shadow_confidence`                   | %     | Confidence derived from learned profile coverage                           |
| `sensor.smart_fan_controller_mpc_shadow_predicted_temperature_10_min` | °C    | Predicted temperature after 10 minutes with the recommended mode           |
| `sensor.smart_fan_controller_mpc_shadow_predicted_temperature_30_min` | °C    | Predicted temperature after 30 minutes with the recommended mode           |
| `sensor.smart_fan_controller_mpc_shadow_dead_time`                    | min   | Dead time currently used by the shadow simulator                           |
| `sensor.smart_fan_controller_mpc_shadow_known_profiles`               | count | Number of reliable learned fan-mode profiles used by the shadow controller |
| `sensor.smart_fan_controller_mpc_shadow_disturbance_bias`             | °C/h  | Learned disturbance correction currently applied by the shadow model       |

See [docs/mpc_shadow_mode.md](docs/mpc_shadow_mode.md) for the full technical design of the learned model and MPC-lite shadow mode.

### Defrost Diagnostic

| Entity                               | Type   | Description                                                 |
| ------------------------------------ | ------ | ----------------------------------------------------------- |
| `sensor.smart_fan_controller_status` | Sensor | Shows `"Defrost hold: …"` when defrost protection is active |

---

## Services

### `smart_fan_controller.apply_learned_settings`

Manually apply the parameters computed by the learning system. Useful when auto-apply is disabled or to re-apply after a manual change.

**Requirement**: `sensor.smart_fan_controller_learning_status` must be `"Ready"`.

### `smart_fan_controller.reset_learning`

Clear all learning data and start fresh. Use after HVAC maintenance or a significant system change.

---

## Troubleshooting

| Symptom                              | What to check / do                                                                                                                                                               |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Fan not changing**                 | Check `sensor.smart_fan_controller_status`. Check `sensor.smart_fan_controller_fan_mode_last_change` — min interval or dead-time patience may be active.                         |
| **Too many fan changes**             | Increase `deadband` or `min_interval`. Enable learning to auto-optimize.                                                                                                         |
| **Temperature overshoots**           | Decrease `deadband`. Verify Versatile Thermostat is providing an accurate slope.                                                                                                 |
| **Learning not progressing**         | Verify `switch.smart_fan_controller_learning_enabled` is on. Check HVAC is running and windows are closed.                                                                       |
| **Auto-apply not working**           | Verify `sensor.smart_fan_controller_learning_status` is `"Ready"` and learning is on. Auto-apply fires once — use `apply_learned_settings` to re-apply.                          |
| **Defrost triggering too often**     | If auto-detection fires spuriously (noisy slope), configure a physical defrost entity from your PAC integration. This replaces heuristic guesses with the actual defrost signal. |
| **Step-down blocked during defrost** | Normal behavior — zones B and D are intentionally suspended during the 20 min defrost cooldown to avoid fan reduction while the PAC recovers.                                    |

---

## License

This project is licensed under the MIT License.
