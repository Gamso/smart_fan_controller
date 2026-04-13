# Smart Fan Controller — Project Guidelines

## Architecture

The integration lives under `custom_components/smart_fan_controller/`. Key modules:

| File | Role |
|------|------|
| `mpc_controller.py` | `MPCController` — MPC-lite: learned thermal model, cost-based fan selection, hysteresis guards |
| `thermal_learning.py` | `ThermalLearning` — slope samples, response-time events, profile calibration |
| `__init__.py` | HA integration entry point: control loop, learning collection, services |
| `config_flow.py` | Config and options UI flows |
| `sensor.py` | HA sensor entity platform |
| `data_collection.py` | CSV logger for offline analysis |

## Domain Vocabulary

- **error**: always positive when the system needs more heating/cooling (`target - current` in heat, `current - target` in cool)
- **effective_slope**: learned slope per fan mode (°C/h, from `ThermalLearning`); raw slope comes from VTherm EMA
- **dead_time**: thermal lag between a fan change and first observable slope response (learned via response events)
- **defrost_active**: heat-pump defrost cycle (via external entity); pauses MPC decisions and learning
- **hvac_idle**: heat-pump compressor is off (via operating entity); pauses MPC decisions and learning
- **ctrl_state**: closure dict in `__init__.py` tracking `last_change_time`, `previous_slope`, `defrost` state, `last_setpoint_drop_time`

## Control Flow (MPC-only)

Each cycle (`__init__.py → run_control_loop`):

1. Read climate entity state (temp, target, slope, fan, window)
2. Detect disturbances (defrost, HVAC idle)
3. Call `mpc_controller.evaluate()` → returns `mpc_decision` dict
4. If MPC status is actionable → apply MPC fan choice; otherwise → hold current fan
5. Collect learning data (slope samples, response events) with gating for phase, defrost, idle, window, setpoint-drop cooldown
6. Update sensors via `sensor.update_from_mpc(mpc_decision)`
7. Log to CSV if data collection enabled

## MPC Decision Engine

The MPC controller (`mpc_controller.py`) evaluates all candidate fan modes over a 30-min horizon:

- **Cost function**: comfort error + overshoot penalty + floor violation + mode-change cost + min-interval penalty
- **Hysteresis**: requires minimum cost improvement before switching (margin scales with proximity to target)
- **Step-down guards**: blocks downward moves when under target and not established or predicted shortfall
- **Disturbance bias**: EMA tracker for unmodeled effects (solar, occupancy); decays during paused periods
- **Monotone constraint**: when all profiles learned, enforces slope(mode_i) ≤ slope(mode_i+1)
- **Pause conditions**: window open, defrost, HVAC idle → returns "Disturbed" status

## Test Conventions

- Framework: **pytest** in `tests/`; run with `python -m pytest tests/ -q`
- Never use `time.sleep`; mock `time.time` via `unittest.mock.patch`
- Protected-member access (`mpc._state`) is normal in tests — add `# pylint: disable=protected-access` at module top
- Fixtures that shadow outer names need `# pylint: disable=redefined-outer-name`
- Every test function and helper needs a docstring\n- Test helpers: `_build_learning()` returns `ThermalLearning()`, `_build_mpc(learning)` returns configured `MPCController`

## Home Assistant Patterns

- `async_write_ha_state()` is called after any state mutation in sensor entities
- Config flow uses `vol.Schema` with selectors from `homeassistant.helpers.selector`
- Entity IDs are built with `build_entity_id()` / `build_unique_id()` from `const.py`
- Data dict: `hass.data[DOMAIN][entry_id]` contains `"learning"`, `"mpc_controller"`, `"climate_entity"`, `"sensors"`, `"store"`

## Build & Test

```bash
python -m pytest tests/ -q          # run all tests
python -m pytest tests/test_X.py -q # run one file
./container coverage                 # coverage via Docker container
./container hassfest                 # validate manifest / translations
```

## Key Constants (const.py)

- `THRESHOLD_TARGET_DROP = -1.0` — setpoint-drop trigger (°C)
- `DEFAULT_DEADBAND` — tunable via options flow
- `CONF_DEFROST_ENTITY` — optional entity for external defrost signal
- `CONF_OPERATING_ENTITY` — optional entity for heat-pump compressor running state
- `SETPOINT_DROP_LEARNING_COOLDOWN = 30.0` — minutes to suppress learning after a large setpoint drop
- `MIN_ESTABLISHED_RATIO = 2.0` — multiplier on dead_time; fan mode must be active this long before learning

## Important Constraints

- **Avoid over-engineering**: only add code that directly addresses the requirement
- **No second-order slope terms**: VTherm slope is already EMA-smoothed; parabolic projection amplifies noise
- **Learning data integrity**: exclude window-open, defrost, HVAC idle, setpoint-drop cooldown (30 min), and insufficiently-stable periods (< 2× dead_time) from slope samples; effective slope uses **median** (not mean) for outlier robustness
- **Monotone constraint**: when all fan-mode profiles are learned, MPC enforces slope(mode_i) ≤ slope(mode_i+1) via isotonic forward pass; partial profiles skip the constraint
