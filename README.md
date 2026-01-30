# Predictive Fan Controller for Versatile Thermostat

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

**Smart, predictive fan speed control for Air Conditioning systems**.

Designed to work seamlessly with Versatile Thermostat for tighter temperature control, improved comfort, and reduced mechanical wear.


## Installation

### HACS (Recommended)

This card is available in HACS (Home Assistant Community Store).

Click the button below to add this repository to HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gamso&bouton&repository=smart_fan_controller&category=integration)



## ✨ Overview

Predictive Fan Controller is a custom Home Assistant component that dynamically adjusts AC fan speed based on thermal prediction rather than reactive temperature changes.<br>
By leveraging Versatile Thermostat’s thermal slope and acceleration data, the controller anticipates temperature evolution and adapts fan output before overshoot or drift occurs.


## 🔧 Core Engine

### Parabolic Thermal Forecasting
Instead of waiting for a temperature change, the controller computes a parabolic forecast using:
- Current thermal slope (velocity)
- Thermal acceleration

This allows proactive fan speed adjustments up to 10 minutes ahead.

### VTherm Data Integration
The controller consumes the vtherm_slope attribute from Versatile Thermostat, transforming raw thermal data into actionable fan control decisions.

### Overshoot Prevention (Braking)
When the forecast predicts the target temperature will be exceeded, the controller automatically steps down fan speed to brake thermal momentum.

### Anti-Cycling Logic
To prevent excessive wear:
- Enforces a minimum interval between fan speed changes
- Detects insignificant slope changes to avoid unnecessary toggling


## 🧠 Decision Logic

The controller maps the thermal environment into 6 operational zones:

| Zone        | Condition             | System Response                          |
| :---------- | :-------------------- | :--------------------------------------- |
| Emergency   | Error > hard_error    | Instant Max Speed (Bypasses timers)      |
| Braking     | Predicted overshoot   | Step Down to prevent target miss         |
| Recovery    | Error > soft_error    | Step Up if the current trend is stagnant |
| Maintenance | Drift in comfort zone | Micro-adjustment to hold the setpoint    |
| Over-Target | Target exceeded       | Step Down to return to setpoint          |
| Stable      | On target & holding   | Maintain current output for efficiency   |


## ⚙️ Configuration Parameters

| Parameter                   | Default | Description                                        |
| --------------------------- | ------- | -------------------------------------------------- |
| `deadband`                  | 0.2°C   | Precision margin for the comfort zone              |
| `min_interval`              | 10 min  | Minimum duty cycle between fan speed transitions   |
| `soft_error`                | 0.3°C   | Trigger point for active thermal correction        |
| `hard_error`                | 0.6°C   | Safety threshold for maximum cooling/heating power |
| `projected_error_threshold` | 0.5°C   | Prediction sensitivity for proactive boosting      |

### 🔄 Reconfiguration Without Restart

The integration supports **configuration hot reload** — you can modify any configuration parameter through the Home Assistant UI without restarting Home Assistant:

1. Go to **Settings** → **Devices & Services**
2. Find **Smart Fan Controller** and click **Configure**
3. Adjust parameters as needed
4. Click **Submit** — changes apply instantly

The integration will automatically reload with the new settings, preserving learned data and continuing operation seamlessly.

> **Note about HACS updates:** When updating to a new version via HACS, a Home Assistant restart is required. This is a limitation of Home Assistant's architecture—Python code changes cannot be hot-reloaded. The reload feature above applies only to configuration parameter changes.
