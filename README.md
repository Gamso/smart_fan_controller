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
  - [✨ Overview](#-overview)
    - [How It Works](#how-it-works)
  - [✅ Requirements](#-requirements)
  - [⚙️ Quick Setup](#️-quick-setup)
  - [⚙️ Configuration Parameters](#️-configuration-parameters)
  - [🧠 Control Logic](#-control-logic)
    - [Decision Priority](#decision-priority)
    - [Temperature Projection](#temperature-projection)
    - [Safety Constraints](#safety-constraints)
  - [🤖 Learning System](#-learning-system)
  - [📊 Sensors & Entities](#-sensors--entities)
  - [🛠️ Services](#️-services)
  - [🔧 Troubleshooting](#-troubleshooting)
  - [📄 License](#-license)

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

## ✨ Overview

Smart Fan Controller is a custom Home Assistant integration that **smoothly adjusts HVAC fan speed** based on how the temperature is evolving, not just the current reading. The result is better comfort, fewer oscillations, and less wear on your equipment.

### How It Works

1. Every 2 minutes, reads the current temperature, target, and temperature slope from Versatile Thermostat
2. Projects temperature **10 minutes ahead** using a parabolic model
3. Selects the appropriate control mode (comfort, recovery, emergency, etc.)
4. Changes fan speed if needed, respecting safety constraints
5. Collects learning data to automatically optimize parameters over time

---

## ✅ Requirements

- **Home Assistant** 2026.2.2 or later
- A **climate entity** with multiple fan speeds (e.g., `low`, `medium`, `high`)
- **Versatile Thermostat** (or compatible integration) that exposes `temperature_slope` in its `specific_states` attribute

---

## ⚙️ Quick Setup

1. Go to **Settings → Devices & Services → Add Integration → Smart Fan Controller**
2. Select your climate entity
3. Configure parameters (or use defaults)
4. Save — the controller starts working immediately

---

## ⚙️ Configuration Parameters

All parameters can be changed at any time via **Settings → Devices & Services → Smart Fan Controller → Configure**.

| Parameter            | Default   | Range               | Description                                                                                     |
| -------------------- | --------- | ------------------- | ----------------------------------------------------------------------------------------------- |
| **Deadband**         | `0.2°C`   | `0.0` – `5.0°C`    | Comfort zone around target — no action taken within this range. Increase to reduce fan changes. |
| **Min Interval**     | `10 min`  | `1` – `60 min`     | Minimum time between non-emergency fan changes. Prevents rapid oscillations.                    |
| **Soft Error**       | `0.3°C`   | `0.0` – `10.0°C`   | Error threshold that triggers recovery mode. Should be larger than deadband.                    |
| **Hard Error**       | `0.6°C`   | `0.0` – `10.0°C`   | Error threshold that triggers emergency mode (max fan, bypasses min interval).                  |
| **Limit Timeout**    | `15 min`  | `10` – `120 min`   | Maximum time before forcing a re-evaluation, even without significant slope change.             |
| **Learning Enabled** | `true`    | —                   | Enables the automatic learning system. Disable for fully manual tuning.                         |

> **Tip — recommended ratios**: `deadband < soft_error < hard_error`, e.g. `0.2 / 0.3 / 0.6`.

---

## 🧠 Control Logic

Each cycle the controller evaluates the current state and applies the first matching rule:

### Decision Priority

| Priority | Condition                                        | Action                          |
| -------- | ------------------------------------------------ | ------------------------------- |
| 1        | `error ≥ hard_error`                             | 🚨 **Emergency** — max fan, immediate |
| 2        | Target dropped by more than 1°C                  | 🌙 **Night mode** — min fan, immediate |
| 3        | Projected error `< -deadband` AND slope changed  | 🛑 **Braking** — decrease fan proactively |
| 4        | `error > soft_error`                             | 📈 **Recovery** — increase fan (or wait if slope improving) |
| 5        | `0 < error < soft_error`                         | ⚠️ **Drift** — gentle adjustment if drifting away |
| 6        | `error < -deadband`                              | ❄️ **Overcooling/Overheating** — decrease fan |
| 7        | `-deadband ≤ error ≤ 0`                          | ✅ **Comfort zone** — no action |

> **Note**: The error is always calculated so a positive value means the system needs more heating or cooling.

### Temperature Projection

The controller uses a parabolic model to predict temperature 10 minutes ahead:

```
thermal_acceleration = (current_slope - previous_slope) / time_delta
projected_temp = current_temp + (slope × 10min) + (0.5 × acceleration × (10min)²)
```

An exponential moving average (EMA) filter is applied to the acceleration to reduce noise.

### Safety Constraints

- **Step-down protection**: Fan speed can only decrease by one step at a time (e.g., `high → medium`), preventing abrupt pressure changes.
- **Min interval**: Non-emergency changes respect the configured min interval. Emergency and night mode override it.

---

## 🤖 Learning System

The integration includes an **automatic learning system** that collects data during normal operation and computes optimal parameters after ~48–72 hours (≥240 samples).

**Data collected every 2 minutes**:
- Temperature slope and fan mode
- Time from fan speed change to next significant slope change (thermal response time)

**Parameters computed from data**:

| Parameter          | Formula                                                     |
| ------------------ | ----------------------------------------------------------- |
| `deadband`         | `0.15 + (volatility_factor × 0.2)`                         |
| `soft_error`       | `0.25 + (volatility_factor × 0.3)`                         |
| `hard_error`       | `0.5 + (volatility_factor × 0.4)`                         |
| `limit_timeout`    | median of measured thermal response times                   |

Where `volatility_factor = min(slope_stdev / slope_mean, 3.0)`.

Once learning is ready, parameters are **automatically applied** and the integration reloads. To apply manually, use the `apply_learned_settings` service. To start over, use `reset_learning`.

**Control**: Enable or disable learning at any time via `switch.smart_fan_learning_enabled`. Existing data is preserved when disabled.

---

## 📊 Sensors & Entities

### Main Entities

| Entity                                      | Type    | Description                                       |
| ------------------------------------------- | ------- | ------------------------------------------------- |
| `sensor.smart_fan_fan_mode`                 | Sensor  | Current fan mode selected by the controller       |
| `sensor.smart_fan_status`                   | Sensor  | Current control mode and decision reason          |
| `switch.smart_fan_learning_enabled`         | Switch  | Enable / disable the learning system              |

### Diagnostic Sensors

| Entity                                         | Unit    | Description                                        |
| ---------------------------------------------- | ------- | -------------------------------------------------- |
| `sensor.smart_fan_temperature_error`           | °C      | Current temperature error (positive = needs action) |
| `sensor.smart_fan_projected_temperature`       | °C      | Predicted temperature 10 minutes ahead             |
| `sensor.smart_fan_projected_temperature_error` | °C      | Predicted error 10 minutes ahead                   |
| `sensor.smart_fan_minutes_since_last_change`   | min     | Time elapsed since last fan mode change            |
| `sensor.smart_fan_learning_progress`           | %       | Learning completion (100% = ≥240 samples)          |
| `sensor.smart_fan_learning_status`             | —       | `"Learning (45%)"` or `"Ready"`                   |
| `sensor.smart_fan_learning_samples`            | count   | Number of slope samples collected                  |
| `sensor.smart_fan_learning_response_events`    | count   | Number of thermal response time measurements       |
| `sensor.smart_fan_learned_deadband`            | °C      | Learned optimal deadband                           |
| `sensor.smart_fan_learned_soft_error`          | °C      | Learned optimal soft error threshold               |
| `sensor.smart_fan_learned_hard_error`          | °C      | Learned optimal hard error threshold               |
| `sensor.smart_fan_learned_limit_timeout`       | min     | Learned optimal limit timeout                      |

---

## 🛠️ Services

### `smart_fan_controller.apply_learned_settings`

Manually apply the parameters computed by the learning system. Useful when auto-apply is disabled or to re-apply after a manual change.

**Requirement**: `sensor.smart_fan_learning_status` must be `"Ready"`.

### `smart_fan_controller.reset_learning`

Clear all learning data and start fresh. Use after HVAC maintenance or a significant system change.

---

## 🔧 Troubleshooting

| Symptom                      | What to check / do                                                                                                   |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **Fan not changing**         | Check `sensor.smart_fan_status`. Check `sensor.smart_fan_minutes_since_last_change` — min interval may be active.   |
| **Too many fan changes**     | Increase `deadband` or `min_interval`. Enable learning to auto-optimize.                                             |
| **Temperature overshoots**   | Decrease `deadband`. Verify Versatile Thermostat is providing an accurate slope.                                     |
| **Learning not progressing** | Verify `switch.smart_fan_learning_enabled` is on. Check HVAC is running and not stuck in night mode.                |
| **Auto-apply not working**   | Verify `sensor.smart_fan_learning_status` is `"Ready"` and learning is on. Auto-apply fires once — use `apply_learned_settings` to re-apply. |

---

## 📄 License

This project is licensed under the MIT License.
