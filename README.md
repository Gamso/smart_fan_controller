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

Predictive Fan Controller is a custom Home Assistant integration that **smoothly adjusts HVAC fan speed** based on how the temperature is evolving, not just the current reading. The result is better comfort, fewer oscillations, and less wear on your equipment.


## ✅ Requirements

- A climate entity with multiple fan speeds
- Versatile Thermostat (or compatible integration)


## ⭐ Key Benefits

- **More stable comfort** with fewer temperature swings
- **Smarter fan speed changes** (no rapid back‑and‑forth)
- **Less mechanical stress** thanks to gentle adjustments
- **Easy to set up** via the Home Assistant UI


## ⚙️ Quick Setup

1. Add the integration in Home Assistant.
2. Select your climate entity.
3. Save — it starts working immediately with sensible defaults.


## 🧩 Advanced Options (optional)

If you want to fine‑tune behavior, you can adjust parameters such as comfort band, minimum delay between changes, and recovery aggressiveness in the integration options.


## 🤖 Auto‑Calibration (Learning)

The integration can learn your system’s behavior over time and suggest improved settings. You can apply those learned settings when you’re ready.


## 🛠️ Services (advanced)

- `smart_fan_controller.apply_learned_settings` — Apply learned values and reload the integration.
- `smart_fan_controller.reset_learning` — Reset learning data and start fresh.
