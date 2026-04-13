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
  - [MPC Controller](#mpc-controller)
    - [Cost Function](#cost-function)
    - [Hysteresis and Guards](#hysteresis-and-guards)
    - [Phase Detection](#phase-detection)
    - [Disturbance Handling](#disturbance-handling)
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
    - [MPC Sensors](#mpc-sensors)
    - [Learning Sensors](#learning-sensors)
    - [Learning Profile Sensors](#learning-profile-sensors)
  - [Services](#services)
    - [`smart_fan_controller.apply_learned_settings`](#smart_fan_controllerapply_learned_settings)
    - [`smart_fan_controller.reset_learning`](#smart_fan_controllerreset_learning)
    - [`smart_fan_controller.set_effective_slope`](#smart_fan_controllerset_effective_slope)
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

Smart Fan Controller is a custom Home Assistant integration that **adjusts HVAC fan speed** using a predictive MPC (Model Predictive Control) engine. It learns the thermal behavior of your room and selects the optimal fan mode to reach and maintain the target temperature.

### How It Works

1. Every 2 minutes, reads the current temperature, target, and temperature slope from Versatile Thermostat
2. Simulates every available fan mode over a **30-minute prediction horizon** using learned thermal profiles
3. Selects the fan mode that **minimizes a cost function** balancing comfort, overshoot, and energy use
4. Applies safety guards (hysteresis, min interval) before changing the fan
5. Continuously **learns** the thermal response of each fan mode to improve predictions over time

> The MPC controller supports both `heat` and `cool` modes. It includes a hysteresis guard so tiny cost differences do not create fan oscillations near the setpoint.
> When all fan-mode profiles are learned, a **monotone constraint** guarantees higher fan modes have steeper slopes than lower ones.

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

| Parameter          | Default  | Range           | Description                                                                                                                                                                         |
| ------------------ | -------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Deadband**       | `0.2°C`  | `0.0` – `5.0°C` | Comfort zone around target — no action taken within this range. Increase to reduce fan changes.                                                                                     |
| **Min Interval**   | `10 min` | `1` – `60 min`  | Minimum time between non-emergency fan changes. Prevents rapid oscillations.                                                                                                        |
| **Limit Timeout**  | `15 min` | `10` – `120 min`| Fallback timeout used before learning calibrates the dead time.                                                                                                                     |
| **Data Collection** | `true`  | —               | Records one CSV row every 2 minutes in the HA config folder (`smart_fan_controller_data_XXXXXXXX.csv`, max 10 MB, auto-rotated). Useful for offline analysis.                       |
| **Defrost Entity** | *(none)* | —               | Optional entity (`binary_sensor`, `sensor`, or `input_boolean`) that reports when the heat pump is in defrost cycle. See [Defrost Detection](#defrost-detection).                   |
| **Operating Entity** | *(none)* | —             | Optional entity (`binary_sensor`, `sensor`, or `input_boolean`) that reports whether the heat pump compressor is actively running. See [HVAC Idle Detection](#hvac-idle-detection). |

---

## MPC Controller

The MPC (Model Predictive Control) engine is the sole decision-maker for fan speed. Each cycle, it evaluates every available fan mode by simulating temperature evolution over a 30-minute horizon and selecting the mode with the lowest cost.

See [docs/mpc_mode.md](docs/mpc_mode.md) for the full technical design.

### Cost Function

Each candidate fan mode is scored with:

| Component                   | Purpose                                                                      |
| --------------------------- | ---------------------------------------------------------------------------- |
| **Comfort error × urgency** | Penalizes being outside the deadband, amplified when far from target         |
| **Overshoot²**              | Strongly penalizes going past the target temperature                         |
| **Floor violation**         | Penalizes predicted temperature dropping below setpoint (linear + quadratic) |
| **Mode-change cost**        | Penalizes unnecessary fan jumps (proportional to step distance)              |
| **Mode-rank cost**          | Slight preference for lower fan speeds (energy savings)                      |
| **Min-interval penalty**    | Blocks changes before the minimum interval has elapsed                       |

### Hysteresis and Guards

- **Hysteresis**: a recommendation that changes the fan must beat the current mode by a minimum cost margin. The margin is larger when near the target (0.30) and smaller when far away (0.10).
- **Step-down hold**: blocks downward moves when still under target and the temperature slope hasn't established yet.
- **Min interval**: non-emergency changes respect the effective timeout (learned dead time × 1.5, or the configured limit timeout).

### Phase Detection

After each fan speed change, the controller classifies elapsed time into three phases:

| Phase           | Condition                             | Meaning                           |
| --------------- | ------------------------------------- | --------------------------------- |
| **DEAD_TIME**   | `elapsed < dead_time`                 | Sensor hasn't reacted yet         |
| **TRANSIENT**   | `dead_time ≤ elapsed < dead_time×1.5` | Sensor starting to respond        |
| **ESTABLISHED** | `elapsed ≥ dead_time × 1.5`           | Slope reflects current fan regime |

The default dead time is 10 minutes. When the learning system is ready, it is replaced by the learned median response time.

### Disturbance Handling

The MPC tracks a **disturbance bias** — an EMA estimate of unmodeled thermal effects (solar gains, occupancy). This correction is added to learned slopes during simulation. The bias only updates during ESTABLISHED phase with a known profile and decays during disturbed periods.

When a disturbance is detected, the MPC pauses and returns "Disturbed" status — the current fan mode is held.

### Defrost Detection

When a heat pump defrosts its outdoor coil, the heat output drops sharply. Without defrost awareness, the controller would misinterpret the falling slope.

**External entity (optional)**: Configure a `binary_sensor`, `sensor`, or `input_boolean` from your PAC integration that reports defrost state. When this entity is `on`/`true`/`1`, defrost protection is activated with a 20-minute cooldown.

**During defrost protection**:
- The MPC pauses and returns "Disturbed" status
- Learning samples are excluded (slope data during defrost corrupts profiles)

### HVAC Idle Detection

When the heat pump compressor is off (setpoint reached, system coasting), the HVAC is not actively heating or cooling.

**Operating entity (optional)**: Configure a `binary_sensor`, `sensor`, or `input_boolean` that reports compressor state. When `off`/`false`/`0`, the compressor is considered idle.

> When no operating entity is configured, HVAC idle detection is disabled.

**During HVAC idle**:
- The MPC pauses and returns "Disturbed" status
- Learning samples are excluded

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
| `limit_timeout` | rounded median of measured thermal response times |

Where `volatility_factor = min(slope_stdev / slope_mean, 3.0)`.

> **Important**: the learned `limit_timeout` is the stored base response estimate. At runtime, non-emergency decisions use `effective_timeout = max(min_interval, dead_time × 1.5)` once learning is ready.

Once learning is ready, parameters are **automatically applied** and the integration reloads. To apply manually, use the `apply_learned_settings` service. To start over, use `reset_learning`.

### Per-Mode Fan Profiles

The learning system tracks the **effective slope per fan mode and HVAC mode** (e.g., "medium in heat" vs "high in cool"). This data provides visibility into which fan speeds are most effective for each mode.

Profiles require at least 10 samples per mode to be considered reliable. Samples are automatically filtered out when:
- Window is open (external disturbance)
- Large setpoint drop occurred (night mode) — including a **30-minute cooldown** after the drop to avoid EMA inertia
- HVAC is idle or defrost is active
- The fan mode hasn't been active long enough (**2× dead time**) for the VTherm EMA to fully reflect the current mode
- The phase is not yet ESTABLISHED

The effective slope is computed as the **median** (not mean) of collected samples, providing robustness against occasional outlier readings caused by thermal inertia from previous high-speed modes.

### Dead Time Calibration

The system measures the **thermal response time** — the delay between a fan speed change and the first observable slope change at the sensor. This median value replaces the default 10-minute dead time, allowing the controller to be patient during the actual thermal lag period and reactive once the effect materializes.

Response events are only recorded when the delay is between 2 and 60 minutes (filtering sensor noise and system-off periods).

### Window-Open Filtering

When Versatile Thermostat reports a window as open (via the `window_manager.window_state` attribute):
- **The MPC pauses** and returns "Disturbed" status — the current fan mode is held
- **Learning data collection stops**, including both per-mode slope samples and response-time events used to learn `dead_time`

This prevents window-open periods from corrupting the learned profiles.

### Defrost Learning Exclusion

Slope samples and response-time events collected during an active defrost period (via external entity, including the 20-minute cooldown) are **not added to learned profiles**. Defrost distorts the effective slope per fan mode and would bias the learning system toward lower heating capacity estimates.

### HVAC Idle Learning Exclusion

Slope samples and response-time events collected while the compressor is detected as idle (via `operating_entity` or `power_entity`) are **not added to learned profiles**. When the compressor is off the measured slope reflects ambient drift rather than active heating or cooling capacity, and recording it would corrupt per-mode profiles and dead-time calibration.

---

## Sensors & Entities

### Main Entities

### MPC Sensors

| Entity                                                         | Unit  | Description                                                       |
| -------------------------------------------------------------- | ----- | ----------------------------------------------------------------- |
| `sensor.smart_fan_controller_mpc_status`                       | —     | MPC state (`Not ready`, `Ready`, `Disturbed`, `Idle`, etc.)   |
| `sensor.smart_fan_controller_mpc_reason`                       | —     | Explanation of the current MPC recommendation                     |
| `sensor.smart_fan_controller_mpc_fan_mode`                     | —     | Fan mode chosen by the MPC                                        |
| `sensor.smart_fan_controller_mpc_would_change_now`             | —     | Whether the MPC would actively change the fan right now           |
| `sensor.smart_fan_controller_mpc_cost`                         | —     | Lowest simulation cost returned by the MPC optimizer              |
| `sensor.smart_fan_controller_mpc_confidence`                   | %     | Confidence derived from learned profile coverage                  |
| `sensor.smart_fan_controller_mpc_predicted_temperature_10_min` | °C    | Predicted temperature after 10 minutes with the recommended mode  |
| `sensor.smart_fan_controller_mpc_predicted_temperature_30_min` | °C    | Predicted temperature after 30 minutes with the recommended mode  |
| `sensor.smart_fan_controller_mpc_dead_time`                    | min   | Dead time currently used by the MPC simulator                     |
| `sensor.smart_fan_controller_mpc_known_profiles`               | count | Number of reliable learned fan-mode profiles                      |
| `sensor.smart_fan_controller_mpc_disturbance_bias`             | °C/h  | Learned disturbance correction currently applied by the MPC model |

### Learning Sensors

| Entity                                                    | Unit  | Description                                         |
| --------------------------------------------------------- | ----- | --------------------------------------------------- |
| `sensor.smart_fan_controller_learning_progress`           | %     | Learning completion (100% = ≥240 samples)           |
| `sensor.smart_fan_controller_learning_status`             | —     | `"Learning (45%)"` or `"Ready"`                     |
| `sensor.smart_fan_controller_learning_samples`            | count | Number of slope samples collected                   |
| `sensor.smart_fan_controller_learning_response_events`    | count | Number of thermal response time measurements        |
| `sensor.smart_fan_controller_learned_dead_time`           | min   | Median learned thermal response delay (`dead_time`) |
| `sensor.smart_fan_controller_effective_timeout`           | min   | Actual non-emergency timeout currently used         |
| `sensor.smart_fan_controller_learned_deadband`            | °C    | Learned optimal deadband                            |
| `sensor.smart_fan_controller_learned_limit_timeout`       | min   | Learned base timeout stored in config               |

### Learning Profile Sensors

Once fan modes are detected, the integration creates per-HVAC-mode profile summary sensors and one effective slope sensor per fan mode:

| Entity (example with `low`/`medium`/`high` fan modes)     | Unit | Description                                        |
| --------------------------------------------------------- | ---- | -------------------------------------------------- |
| `sensor.smart_fan_controller_mpc_heat_profiles`           | —    | JSON summary of learned heat profiles per fan mode |
| `sensor.smart_fan_controller_mpc_cool_profiles`           | —    | JSON summary of learned cool profiles per fan mode |
| `sensor.smart_fan_controller_heat_low_effective_slope`    | °C/h | Effective slope learned for `low` in heat mode     |
| `sensor.smart_fan_controller_heat_medium_effective_slope` | °C/h | Effective slope learned for `medium` in heat mode  |
| `sensor.smart_fan_controller_heat_high_effective_slope`   | °C/h | Effective slope learned for `high` in heat mode    |
| `sensor.smart_fan_controller_cool_low_effective_slope`    | °C/h | Effective slope learned for `low` in cool mode     |
| … (one per fan mode × HVAC mode combination)              | …    | …                                                  |

These sensors appear automatically when the climate entity's fan modes become known and require at least 10 samples per mode to show reliable data.

---

## Services

### `smart_fan_controller.apply_learned_settings`

Manually apply the parameters computed by the learning system. Useful when auto-apply is disabled or to re-apply after a manual change.

**Requirement**: `sensor.smart_fan_controller_learning_status` must be `"Ready"`.

### `smart_fan_controller.reset_learning`

Clear all learning data and start fresh. Use after HVAC maintenance or a significant system change.

### `smart_fan_controller.set_effective_slope`

Manually set the effective slope for a specific fan mode / HVAC mode profile without resetting all learning data. Replaces existing samples for that profile with synthetic ones matching the provided slope.

**Parameters**:

| Parameter         | Required | Example  | Description                                                |
| ----------------- | -------- | -------- | ---------------------------------------------------------- |
| `hvac_mode`       | Yes      | `heat`   | The HVAC mode (`heat` or `cool`)                           |
| `fan_mode`        | Yes      | `silent` | The fan mode name                                          |
| `effective_slope` | Yes      | `0.15`   | Target effective slope in °C/h (positive = towards target) |

**Example** (Developer Tools → Services):
```yaml
service: smart_fan_controller.set_effective_slope
data:
  hvac_mode: heat
  fan_mode: silent
  effective_slope: 0.15
```

---

## Troubleshooting

| Symptom                          | What to check / do                                                                                                                                    |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Fan not changing**             | Check `sensor.smart_fan_controller_mpc_status` and `mpc_reason`. The MPC may be paused (disturbed) or the min interval hasn't elapsed.               |
| **MPC status: Not ready**        | Learning hasn't collected enough profiles. Check `sensor.smart_fan_controller_mpc_known_profiles` and `learning_progress`.                            |
| **MPC status: Disturbed**        | Defrost, HVAC idle, or window open detected. Normal — MPC holds current fan until the disturbance clears.                                             |
| **Too many fan changes**         | Increase `deadband` or `min_interval`. Enable learning to auto-optimize.                                                                              |
| **Temperature overshoots**       | Decrease `deadband`. Verify Versatile Thermostat is providing an accurate slope.                                                                      |
| **Learning not progressing**     | Verify HVAC is running and windows are closed.                                            |
| **Auto-apply not working**       | Verify `sensor.smart_fan_controller_learning_status` is `"Ready"`. Auto-apply fires once — use `apply_learned_settings` to re-apply. |

---

## License

This project is licensed under the MIT License.
