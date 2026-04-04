# Smart Fan Controller — Project Guidelines

## Architecture

The integration lives under `custom_components/smart_fan_controller/`. Key modules:

| File | Role |
|------|------|
| `controller.py` | `SmartFanController` — rule-based decision engine (zones A–F) |
| `mpc_shadow.py` | `MPCShadowController` — observation-only MPC-lite, never writes fan commands |
| `thermal_learning.py` | `ThermalLearning` — slope samples, response-time events, profile calibration |
| `__init__.py` | HA integration entry point: config, control loop, services |
| `config_flow.py` | Config and options UI flows |
| `sensor.py` / `switch.py` | HA entity platforms |
| `data_collection.py` | CSV logger for offline analysis |

## Domain Vocabulary

- **error**: always positive when the system needs more heating/cooling (`target - current` in heat, `current - target` in cool)
- **effective_slope**: `vtherm_slope` (°C/h, EMA-smoothed by Versatile Thermostat)
- **dead_time**: thermal lag between a fan change and first observable slope response
- **defrost_active**: heat-pump defrost cycle (auto-detected or via external entity); blocks step-down decisions and learning samples
- **hvac_idle**: heat-pump compressor is off (detected via operating entity or power consumption); blocks step-up decisions (zones C, D) and learning samples — no cooldown unlike defrost
- **Zones A–F**: priority-ordered decision rules; zone A = emergency/setpoint-drop, F = comfort hold

## Decision Engine Zones (in priority order)

| Zone   | Condition                                           | Action needed to modify       |
|--------|-----------------------------------------------------|-------------------------------|
| A      | `error ≥ hard_error` OR `error < TARGET_DROP (-1°C)` | `controller.py:_decide_zone_a` |
| B      | Projected overshoot > deadband AND slope changed    | `controller.py:_decide_zone_b` |
| C      | `error > soft_error`                                | `controller.py:_decide_zone_c` |
| D      | `0 < error < soft_error`                            | `controller.py:_decide_zone_d` |
| E      | `error < -deadband`                                 | `controller.py:_decide_zone_e` |
| F      | `-deadband ≤ error ≤ 0`                             | `controller.py:_decide_zone_f` |

## Test Conventions

- Framework: **pytest** in `tests/`; run with `python -m pytest tests/ -q`
- Never use `time.sleep`; mock `time.time` via `unittest.mock.patch`
- Protected-member access (`controller._state`) is normal in tests — add `# pylint: disable=protected-access` at module top
- Fixtures that shadow outer names need `# pylint: disable=redefined-outer-name`
- Every test function and helper needs a docstring
- HA switch classes only implement `async_turn_on`/`async_turn_off` — add `# pylint: disable=abstract-method` on the class

## Home Assistant Patterns

- Switches inherit `SwitchEntity` and only override async variants; sync `turn_on`/`turn_off` are intentionally omitted
- `async_write_ha_state()` is called after any state mutation in switch/sensor entities
- Config flow uses `vol.Schema` with selectors from `homeassistant.helpers.selector`
- Entity IDs are built with `build_entity_id()` / `build_unique_id()` from `const.py`

## Build & Test

```bash
python -m pytest tests/ -q          # run all tests
python -m pytest tests/test_X.py -q # run one file
./container coverage                 # coverage via Docker container
./container hassfest                 # validate manifest / translations
```

## Key Constants (const.py)

- `THRESHOLD_TARGET_DROP = -1.0` — setpoint-drop trigger (°C)
- `DEFAULT_DEADBAND`, `DEFAULT_SOFT_ERROR`, `DEFAULT_HARD_ERROR` — all tunable via options flow
- `CONF_DEFROST_ENTITY` — optional entity for external defrost signal
- `CONF_OPERATING_ENTITY` — optional entity for heat-pump compressor running state
- `CONF_POWER_ENTITY` — optional sensor for heat-pump power consumption (idle detection fallback)
- `DEFAULT_IDLE_POWER_THRESHOLD = 20` — below this wattage the compressor is considered idle

## Important Constraints

- **Avoid over-engineering**: only add code that directly addresses the requirement
- **No second-order slope terms**: VTherm slope is already EMA-smoothed; parabolic projection amplifies noise
- **One step at a time**: fan speed changes are always limited to ±1 step (braking, recovery)
- **MPC shadow is read-only**: it must never call any HA service or modify controller state
protoart climate control- **MPC shadow pauses during defrost**: like window-open, it returns "Disturbed" and decays the disturbance bias without updating it
- **MPC shadow pauses during HVAC idle**: same as defrost — returns "Disturbed" and decays the disturbance bias
- **Learning data integrity**: exclude window-open, defrost, and HVAC idle periods from slope samples and response-time events
