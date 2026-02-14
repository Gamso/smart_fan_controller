# Predictive Fan Controller for Versatile Thermostat

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

**Smart, predictive fan speed control for Air Conditioning systems**.

Designed to work seamlessly with Versatile Thermostat for tighter temperature control, improved comfort, and reduced mechanical wear.

---

## Table of Contents

- [Installation](#installation)
- [Overview](#-overview)
- [Requirements](#-requirements)
- [Key Benefits](#-key-benefits)
- [Quick Setup](#️-quick-setup)
- [Technical Architecture](#-technical-architecture)
- [Configuration Parameters](#️-configuration-parameters)
- [Control Logic & Decision Algorithm](#-control-logic--decision-algorithm)
- [Learning System](#-learning-system)
- [Sensors & Entities](#-sensors--entities)
- [Services](#️-services)
- [Troubleshooting](#-troubleshooting)
- [Examples & Use Cases](#-examples--use-cases)

---

## Installation

### HACS (Recommended)

This integration is available in HACS (Home Assistant Community Store).

Click the button below to add this repository to HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gamso&repository=smart_fan_controller&category=integration)

### Manual Installation

1. Copy the `custom_components/smart_fan_controller` directory to your Home Assistant `custom_components` folder
2. Restart Home Assistant
3. Add the integration via the UI (Settings → Devices & Services → Add Integration)

---

## ✨ Overview

Predictive Fan Controller is a custom Home Assistant integration that **smoothly adjusts HVAC fan speed** based on how the temperature is evolving, not just the current reading. The result is better comfort, fewer oscillations, and less wear on your equipment.

### How It Works

Instead of reacting only to the current temperature error, this integration:
- **Monitors temperature trends** (slope from Versatile Thermostat)
- **Projects future temperature** 10 minutes ahead using thermal acceleration
- **Anticipates overshoots** and adjusts fan speed proactively
- **Learns your system's behavior** to optimize parameters automatically

---

## ✅ Requirements

- **Home Assistant** 2023.1 or later
- A **climate entity** with multiple fan speeds (e.g., `low`, `medium`, `high`)
- **Versatile Thermostat** (or compatible integration) that exposes `temperature_slope` in `specific_states` attribute

---

## ⭐ Key Benefits

- **More stable comfort** with fewer temperature swings
- **Smarter fan speed changes** (no rapid back‑and‑forth)
- **Less mechanical stress** thanks to gentle adjustments
- **Predictive control** anticipates temperature changes before they happen
- **Auto-learning** adapts to your specific HVAC system
- **Easy to set up** via the Home Assistant UI

---

## ⚙️ Quick Setup

1. Add the integration in Home Assistant (Settings → Devices & Services → Add Integration)
2. Select your climate entity with fan modes
3. Configure parameters (or use defaults)
4. Save — it starts working immediately

The integration will:
- Run every 2 minutes to evaluate and adjust fan speed
- Collect learning data automatically (if enabled)
- Create diagnostic sensors for monitoring

---

## 🏗 Technical Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                   Smart Fan Controller                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐      ┌────────────────┐                  │
│  │   Control    │◄────►│   Thermal      │                  │
│  │   Loop       │      │   Learning     │                  │
│  │  (2 min)     │      │   System       │                  │
│  └──────┬───────┘      └────────────────┘                  │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────────────────────────────┐                  │
│  │    Decision Algorithm                 │                  │
│  │  • Temperature projection             │                  │
│  │  • Error analysis                     │                  │
│  │  • Mode selection                     │                  │
│  └──────────────────────────────────────┘                  │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────┐      ┌────────────────┐                  │
│  │   Fan Mode   │      │    Sensors     │                  │
│  │   Output     │      │   & Switch     │                  │
│  └──────────────┘      └────────────────┘                  │
└─────────────────────────────────────────────────────────────┘
         │                                │
         ▼                                ▼
  ┌─────────────┐              ┌──────────────────┐
  │   Climate   │              │   Diagnostics    │
  │   Entity    │              │   & Monitoring   │
  └─────────────┘              └──────────────────┘
```

### Core Algorithm

1. **Data Collection**: Every 2 minutes, read temperature, target, slope, and current fan mode
2. **Projection**: Compute temperature 10 minutes ahead using parabolic projection
3. **Decision**: Apply control logic based on current error and projected error
4. **Action**: Change fan mode if needed (respecting safety constraints)
5. **Learning**: Record data for auto-calibration (if enabled)

---

## ⚙️ Configuration Parameters

All parameters can be configured via the Home Assistant UI (Integration Options).

### Deadband

**Default**: `0.2°C`
**Range**: `0.0` - `5.0°C` (step: 0.05)

The comfort zone around the target temperature where no action is needed.

- **Lower values** (e.g., 0.1°C): More reactive, tighter control, more frequent fan changes
- **Higher values** (e.g., 0.5°C): More stable, less reactive, fewer fan changes

**When to adjust**:
- Increase if you notice too many fan speed changes
- Decrease if temperature doesn't stabilize precisely at target

**Example**:
- Target: 21°C, Deadband: 0.2°C
- **Cooling mode**:
  - Comfort zone: 20.8°C to 21.0°C (error between -0.2 and 0)
  - At 20.9°C: error = -0.1°C → Comfort zone ✓
  - At 21.1°C: error = +0.1°C → Small drift, gentle adjustments
  - At 20.7°C: error = -0.3°C → Overcooling, reduce fan
- **Heating mode**:
  - Comfort zone: 21.0°C to 21.2°C (error between -0.2 and 0)
  - At 21.1°C: error = -0.1°C → Comfort zone ✓
  - At 20.9°C: error = +0.1°C → Small drift, gentle adjustments
  - At 21.3°C: error = -0.3°C → Overheating, reduce fan

Note: The deadband defines when temperature error is acceptable (between -deadband and 0). The comfort zone shifts based on HVAC mode.

---

### Min Interval

**Default**: `10 minutes`
**Range**: `1` - `60 minutes` (step: 1)

Minimum time between non-emergency fan speed changes.

This prevents rapid oscillations and mechanical wear.

**Emergency overrides** this limit when:
- Temperature error ≥ Hard Error threshold
- Target drops significantly (night mode)

**When to adjust**:
- Increase (15-20 min) for slower, more stable systems
- Decrease (5-8 min) for faster-responding systems

**Example**:
```
10:00 - Fan changes from low → medium
10:08 - Controller wants to change again, but blocked by min_interval
10:10 - Change allowed (10 minutes elapsed)
```

---

### Soft Error

**Default**: `0.3°C`
**Range**: `0.0` - `10.0°C` (step: 0.05)

Temperature error threshold for triggering **recovery mode**.

When `|current_temp - target| > soft_error`, the controller enters recovery mode to bring temperature back toward target.

**Relationship with other parameters**:
- Should be larger than `deadband`
- Should be smaller than `hard_error`
- Typical: `soft_error = 1.5 × deadband`

**When to adjust**:
- Increase if system reacts too aggressively to small deviations
- Decrease if system is too slow to recover from errors

**Example**:
- Target: 21°C, Soft Error: 0.3°C
- **Heating mode**: Current 20.6°C → Error = +0.4°C (too cold) → Recovery mode activated
- **Cooling mode**: Current 21.4°C → Error = +0.4°C (too hot) → Recovery mode activated

---

### Hard Error

**Default**: `0.6°C`
**Range**: `0.0` - `10.0°C` (step: 0.05)

Temperature error threshold for triggering **emergency mode**.

When `error ≥ hard_error`, the controller immediately sets fan to **maximum speed**, overriding the min_interval constraint. The error is always calculated so positive values indicate the system needs more heating/cooling.

**Relationship with other parameters**:
- Should be larger than `soft_error`
- Typical: `hard_error = 2 × soft_error`

**When to adjust**:
- Increase if emergency mode triggers too often
- Decrease if system doesn't react fast enough to large errors

**Example**:
- Target: 21°C, Hard Error: 0.6°C
- **Cooling mode**: Current 21.7°C → Error = +0.7°C → Emergency mode! Fan → max immediately
- **Heating mode**: Current 20.3°C → Error = +0.7°C → Emergency mode! Fan → max immediately

---

### Limit Timeout

**Default**: `15 minutes`
**Range**: `10` - `120 minutes` (step: 5)

Maximum time to wait before forcing a fan speed change, even if slope hasn't changed significantly.

This ensures the system remains responsive even when temperature is slowly drifting.

**Behavior**:
- If `minutes_since_last_change ≥ limit_timeout`, allow fan change
- Works as a "timeout" for the min_interval protection

**When to adjust**:
- Increase (20-30 min) for very stable systems
- Decrease (10-12 min) if system tends to drift slowly

**Example with learning**:
The learning system can automatically optimize this parameter based on your HVAC's thermal response time (measured as time from fan change to slope change).

---

### Learning Enabled

**Default**: `True`
**Control**: Via `switch.smart_fan_learning_enabled`

Enables or disables the automatic learning system.

**When enabled**:
- Collects temperature slope samples during normal operation
- Records thermal response times (fan change → slope change)
- Computes optimal parameters after ~48-72 hours
- Auto-applies learned settings when ready

**When disabled**:
- No data collection
- Manual parameter tuning only
- Existing learning data is preserved

**When to disable**:
- During maintenance or testing
- If you prefer manual tuning
- When HVAC system configuration changes (disable, reset, re-enable)

---

## 🧠 Control Logic & Decision Algorithm

The controller uses a **state-based decision algorithm** with predictive capabilities.

### Decision States

```
┌─────────────────────────────────────────────────────────────┐
│                     Decision Tree                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Current Error ≥ Hard Error?                                │
│      ├─ YES → 🚨 EMERGENCY MODE (max fan, force)           │
│      └─ NO → Continue...                                    │
│                                                              │
│  Target Drop ≤ -1.0°C?                                      │
│      ├─ YES → 🌙 NIGHT MODE (min fan, force)               │
│      └─ NO → Continue...                                    │
│                                                              │
│  Projected Error < -Deadband AND Slope Changed?             │
│      ├─ YES → 🛑 BRAKING (decrease fan)                    │
│      └─ NO → Continue...                                    │
│                                                              │
│  Current Error > Soft Error?                                │
│      ├─ YES → 📈 RECOVERY MODE                             │
│      │         ├─ Slope improving? → Wait                   │
│      │         └─ Else → Increase fan                       │
│      └─ NO → Continue...                                    │
│                                                              │
│  Current Error > 0?                                         │
│      ├─ YES → ⚠️ DRIFT MODE                                │
│      │         └─ Check slope, adjust if needed             │
│      └─ NO → Continue...                                    │
│                                                              │
│  Current Error < -Deadband?                                 │
│      ├─ YES → ❄️ OVERCOOLING (decrease fan)                │
│      └─ NO → ✅ COMFORT ZONE (stable)                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Operational Modes Explained

#### 🚨 Emergency Mode
**Trigger**: `error ≥ hard_error`

- Sets fan to **maximum speed** immediately
- **Overrides** min_interval constraint
- Used when temperature is dangerously far from target (too cold in heating mode, or too hot in cooling mode)

**Example**:
- Cooling: Room at 25°C, target 21°C, hard_error 0.6°C → Error = 4°C → Immediate max fan
- Heating: Room at 17°C, target 21°C, hard_error 0.6°C → Error = 4°C → Immediate max fan

---

#### 🌙 Night Mode (Setpoint Drop)
**Trigger**: `error < -1.0°C`

- Detects when target was lowered significantly (e.g., night setback)
- Immediately sets fan to minimum
- Avoids unnecessary cooling when setpoint drops

**Example**: Target drops from 21°C to 18°C → Immediate min fan

---

#### 🛑 Braking Anticipation
**Trigger**: `projected_error < -deadband` AND `slope_changed`

- Predicts temperature will overshoot target
- Reduces fan speed proactively
- Prevents overshoot before it happens

**Example**:
- Cooling toward 21°C
- Current: 21.3°C, projected in 10 min: 20.7°C
- Deadband: 0.2°C → Projected below 20.8°C
- Action: Reduce fan to slow down cooling

---

#### 📈 Recovery Mode
**Trigger**: `error > soft_error`

- Temperature deviating from target
- Two sub-modes:
  - **Patience**: If slope is improving, wait and observe
  - **Recovery**: If slope not improving, increase fan

**Example**:
- Target: 21°C, Current: 20.5°C, Soft Error: 0.3°C
- Error = 0.5°C > 0.3°C → Recovery needed
- If slope positive and increasing → Increase fan

---

#### ⚠️ Drift Mode
**Trigger**: `0 < error < soft_error`

- Small positive error (within soft error threshold)
- Checks if temperature is drifting away
- Adjusts fan if needed to maintain target

**Example**:
- Target: 21°C, Current: 21.15°C
- Small drift detected → Gentle fan increase

---

#### ❄️ Overcooling/Overheating
**Trigger**: `error < -deadband`

- Temperature below target (cooling) or above (heating)
- Reduces fan speed to avoid further deviation

**Example**:
- Target: 21°C, Current: 20.7°C, Deadband: 0.2°C
- Below comfort zone → Reduce fan

---

#### ✅ Comfort Zone
**Trigger**: `-deadband ≤ error ≤ deadband`

- Temperature within acceptable range
- Maintains current fan unless slow drift detected
- Most stable state

---

### Temperature Projection Algorithm

The controller uses a **parabolic projection** model to predict temperature 10 minutes ahead.

```python
# Simplified projection formula
thermal_acceleration = (current_slope - previous_slope) / time_delta
projected_temp = current_temp + (slope × 10min) + (0.5 × acceleration × (10min)²)
```

**Key features**:
- Uses Versatile Thermostat's smoothed temperature slope
- Applies exponential moving average (EMA) filter on acceleration
- 10-minute prediction window balances reactivity and stability

---

### Safety Constraints

#### Step-Down Protection
Fan speed can only **decrease by one step at a time**, but can increase by multiple steps.

**Example**:
- Current: `turbo`, Target: `low`
- Step 1: `turbo` → `high`
- Step 2: `high` → `medium`
- Step 3: `medium` → `low`

**Reason**: Prevents abrupt pressure changes and mechanical stress.

---

#### Min Interval Protection
Non-emergency fan changes respect the `min_interval` parameter.

**Exceptions** (forced changes):
- Emergency mode (high error)
- Night mode (setpoint drop)

---

## 🤖 Learning System

The Smart Fan Controller includes an **automatic learning system** that observes your HVAC behavior and optimizes parameters.

### How Learning Works

```
┌─────────────────────────────────────────────────────────────┐
│                  Learning Data Flow                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Every 2 minutes (during normal operation):                 │
│                                                              │
│  ┌────────────────────────────────────┐                    │
│  │  1. Collect Slope Sample            │                    │
│  │     - Fan mode                      │                    │
│  │     - Temperature slope (°C/h)      │                    │
│  │     - Temperature error             │                    │
│  │     Filters:                        │                    │
│  │     • Skip if |slope| < 0.15        │                    │
│  │     • Skip if error < -1.0°C        │                    │
│  └────────────────────────────────────┘                    │
│             │                                                │
│             ▼                                                │
│  ┌────────────────────────────────────┐                    │
│  │  2. Track Response Times            │                    │
│  │     Time from fan change to         │                    │
│  │     significant slope change        │                    │
│  │     Range: 2-60 minutes             │                    │
│  └────────────────────────────────────┘                    │
│             │                                                │
│             ▼                                                │
│  ┌────────────────────────────────────┐                    │
│  │  3. Compute Statistics              │                    │
│  │     • Mean absolute slope           │                    │
│  │     • Slope variance (Welford)      │                    │
│  │     • Maximum slope                 │                    │
│  │     • Median response time          │                    │
│  └────────────────────────────────────┘                    │
│             │                                                │
│             ▼                                                │
│  ┌────────────────────────────────────┐                    │
│  │  4. Readiness Check                 │                    │
│  │     ≥ 240 samples collected?        │                    │
│  │     (~48-72 hours typical)          │                    │
│  └────────────────────────────────────┘                    │
│             │                                                │
│             ▼ YES                                            │
│  ┌────────────────────────────────────┐                    │
│  │  5. Compute Optimal Parameters      │                    │
│  │     Based on volatility & response  │                    │
│  └────────────────────────────────────┘                    │
│             │                                                │
│             ▼                                                │
│  ┌────────────────────────────────────┐                    │
│  │  6. Auto-Apply (if enabled)         │                    │
│  │     Update config & reload          │                    │
│  └────────────────────────────────────┘                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Data Collection

#### Slope Samples
Collected every 2 minutes during normal operation.

**Each sample contains**:
- Timestamp
- Current fan mode
- Temperature slope (from Versatile Thermostat)
- Temperature error

**Filtering**:
- Ignores stagnation (`|slope| < 0.15°C/h`)
- Ignores night mode / setpoint drops (`error < -1.0°C`)
- Keeps data within 7-day sliding window

---

#### Response Times
Measures thermal inertia of your HVAC system.

**Measurement**: Time from fan speed change to next significant slope change (threshold: 0.1°C/h)

**Valid range**: 2-60 minutes
- Too short (<2 min): Likely noise
- Too long (>60 min): System off or other issue

**Example**:
```
10:00 - Fan: low → medium
10:12 - Slope changes from 0.5 to 1.2°C/h (significant)
→ Response time: 12 minutes
```

---

### Optimal Parameter Computation

After collecting ≥240 samples (~48-72 hours), the system computes optimal values.

#### Deadband Calculation
```python
volatility_factor = min(slope_stdev / slope_mean, 3.0)
optimal_deadband = 0.15 + (volatility_factor × 0.2)
```

- **Low volatility** (stable system): Smaller deadband (tighter control)
- **High volatility** (oscillating system): Larger deadband (more tolerance)

---

#### Soft Error Calculation
```python
optimal_soft_error = 0.25 + (volatility_factor × 0.3)
```

Scales with system volatility to prevent over-reaction.

---

#### Hard Error Calculation
```python
optimal_hard_error = 0.5 + (volatility_factor × 0.4)
```

Emergency threshold adapts to system behavior.

---

#### Limit Timeout Calculation
```python
optimal_limit_timeout = median(response_times)
```

- Uses **median** (not mean) for robustness against outliers
- Directly reflects observed thermal inertia
- No artificial bounds or multipliers

**Example**:
- Response times: [8, 10, 11, 9, 45, 10, 12] minutes
- Median: 10 minutes
- Optimal limit_timeout: 10 minutes

---

### Auto-Apply Functionality

When learning reaches "Ready" status AND learning is enabled:

1. **Compute** optimal parameters
2. **Update** config entry with new values
3. **Log** the changes
4. **Reload** integration to apply
5. **Mark** as applied (prevents re-application)

**User visibility**:
- `sensor.smart_fan_learning_status` shows "Ready"
- `sensor.smart_fan_learned_*` sensors show computed values
- Log message confirms application

---

### Learning Control

Enable/disable via `switch.smart_fan_learning_enabled`

**When disabled**:
- No new data collected
- Existing data preserved
- Can re-enable anytime

**When to reset** (service: `smart_fan_controller.reset_learning`):
- After HVAC maintenance
- System configuration changed
- Want to start fresh learning

---

### Persistence

Learning data is automatically saved:
- **Every 5 minutes** to persistent storage
- **On integration reload/unload**
- Survives Home Assistant restarts
- Stored per integration instance

---

## 📊 Sensors & Entities

The integration creates multiple sensors and entities for monitoring and control.

### Main Sensors

#### `sensor.smart_fan_fan_mode`
Current fan mode selected by the controller.

**Type**: Enum
**Category**: None (main entity)
**Example**: `medium`

---

#### `sensor.smart_fan_status`
Current decision reason and operating mode.

**Type**: String
**Category**: Diagnostic
**Examples**:
- `"Comfort: Stable"`
- `"Recovery: Drop predicted to 20.85°C"`
- `"Emergency: High error (0.75°C)"`

---

#### `sensor.smart_fan_temperature_error`
Current temperature error (current - target, adjusted for HVAC mode).

**Type**: Temperature (°C)
**Category**: Diagnostic
**Positive**: Need more cooling/heating
**Negative**: Too cold/hot

---

#### `sensor.smart_fan_projected_temperature`
Predicted temperature 10 minutes ahead.

**Type**: Temperature (°C)
**Category**: Diagnostic
**Use**: See what controller anticipates

---

#### `sensor.smart_fan_projected_temperature_error`
Predicted error 10 minutes ahead.

**Type**: Temperature (°C)
**Category**: Diagnostic
**Use**: Understand anticipatory actions

---

#### `sensor.smart_fan_minutes_since_last_change`
Time elapsed since last fan mode change.

**Type**: Duration (minutes)
**Category**: Diagnostic
**Use**: Monitor min_interval protection

---

### Learning Sensors

#### `sensor.smart_fan_learning_progress`
Learning completion percentage.

**Type**: Percentage (%)
**Category**: Diagnostic
**Attributes**:
- `samples_collected`: Number of slope samples
- `response_events`: Number of response time measurements
- `is_ready`: Boolean (≥240 samples)
- `learned_*`: Computed optimal parameters

---

#### `sensor.smart_fan_learning_status`
Learning readiness status.

**Type**: String
**Category**: Diagnostic
**Values**:
- `"Learning (45%)"`
- `"Ready"`

---

#### `sensor.smart_fan_learning_samples`
Number of slope samples collected.

**Type**: Count
**Category**: Diagnostic
**Attributes**: Sample statistics (mean, stdev, max)

---

#### `sensor.smart_fan_learning_response_events`
Number of response time measurements.

**Type**: Count
**Category**: Diagnostic
**Attributes**: Response time statistics

---

#### `sensor.smart_fan_learned_deadband`
Learned optimal deadband value.

**Type**: Temperature (°C)
**Category**: Diagnostic
**Updates**: When learning ready

---

#### `sensor.smart_fan_learned_soft_error`
Learned optimal soft error threshold.

**Type**: Temperature (°C)
**Category**: Diagnostic

---

#### `sensor.smart_fan_learned_hard_error`
Learned optimal hard error threshold.

**Type**: Temperature (°C)
**Category**: Diagnostic

---

#### `sensor.smart_fan_learned_limit_timeout`
Learned optimal limit timeout.

**Type**: Duration (minutes)
**Category**: Diagnostic

---

### Switch Entity

#### `switch.smart_fan_learning_enabled`
Controls learning data collection.

**Type**: Switch
**Category**: Config
**Default**: On
**Persists**: Through config changes

---

## 🛠️ Services

### `smart_fan_controller.apply_learned_settings`

Manually apply learned parameter values.

**When to use**:
- Auto-apply is disabled
- Want to review before applying
- Re-apply after manual changes

**Requirements**:
- Learning status must be "Ready"

**Actions**:
1. Retrieves optimal parameters
2. Updates integration config
3. Reloads integration

---

### `smart_fan_controller.reset_learning`

Reset all learning data and start fresh.

**When to use**:
- After HVAC maintenance
- System behavior changed
- Want to restart learning

**Actions**:
1. Clears all slope samples
2. Clears all response events
3. Resets statistics
4. Saves empty learning data

---

## 🔧 Troubleshooting

### Fan not changing

**Check**:
1. `sensor.smart_fan_status` - What's the reason?
2. `sensor.smart_fan_minutes_since_last_change` - Is min_interval blocking?
3. Climate entity state - Is it responding to commands?

**Common causes**:
- Min interval protection active (wait for timeout)
- Temperature within comfort zone (no action needed)
- Climate entity not accepting commands

---

### Too many fan changes

**Solutions**:
1. **Increase `deadband`** (e.g., 0.2 → 0.3°C)
2. **Increase `min_interval`** (e.g., 10 → 15 minutes)
3. **Enable learning** to auto-optimize

**Check**: Are changes happening within min_interval? If so, they're emergency mode.

---

### Temperature overshoots target

**Solutions**:
1. **Decrease `deadband`** for tighter control
2. Check that Versatile Thermostat is providing accurate slope
3. **Increase `limit_timeout`** to allow more reaction time

**Note**: Some overshoot is normal with aggressive heating/cooling.

---

### Learning not progressing

**Check**:
1. `switch.smart_fan_learning_enabled` - Is it on?
2. `sensor.smart_fan_learning_samples` - Are samples being collected?
3. System running normally? (Not in constant emergency mode)

**Common causes**:
- Learning disabled
- HVAC system off most of the time
- Temperature constantly in night mode/setpoint drops

---

### Learning progress stuck below 100%

**Normal**: Learning uses a sliding 7-day window. Old samples expire, so progress may fluctuate.

**Once "Ready" status is reached**, it stays ready even if sample count drops slightly.

---

### Auto-apply not working

**Check**:
1. `sensor.smart_fan_learning_status` - Is it "Ready"?
2. `switch.smart_fan_learning_enabled` - Must be On
3. Check Home Assistant logs for application message

**Note**: Auto-apply happens **once** when learning first becomes ready. If already applied, use service `apply_learned_settings` to re-apply.

---

## 💡 Examples & Use Cases

### Example 1: Basic Residential AC

**Setup**:
- Climate: Air conditioner with 3 speeds (low, medium, high)
- Target: 21°C
- Typical error: ±0.5°C

**Recommended parameters**:
```yaml
deadband: 0.2          # Tight comfort zone
min_interval: 10       # Standard interval
soft_error: 0.3        # React to small deviations
hard_error: 0.6        # Emergency at moderate error
limit_timeout: 15      # Standard timeout
learning_enabled: true # Let it optimize
```

**Expected behavior**:
- Smooth transitions between fan speeds
- Rarely hits emergency mode
- Temperature stable within ±0.3°C
- 3-4 fan changes per hour typical

---

### Example 2: Slow-Responding System

**Setup**:
- Large room or sluggish HVAC
- Takes 20+ minutes to see temperature change

**Recommended parameters**:
```yaml
deadband: 0.3          # More tolerance
min_interval: 15       # Longer between changes
soft_error: 0.4        # Less reactive
hard_error: 0.8        # Higher emergency threshold
limit_timeout: 25      # Longer timeout for slow system
learning_enabled: true # Will learn optimal timeout
```

**Expected behavior**:
- Fewer fan changes
- More patience with temperature drift
- Learning will likely increase limit_timeout to 20-30 minutes

---

### Example 3: Fast-Responding Mini-Split

**Setup**:
- Ductless mini-split
- Very responsive (changes visible in 5 minutes)

**Recommended parameters**:
```yaml
deadband: 0.15         # Very tight control possible
min_interval: 8        # Can change more frequently
soft_error: 0.25       # React quickly
hard_error: 0.5        # Lower emergency threshold
limit_timeout: 12      # Shorter timeout
learning_enabled: true # Will learn fast response
```

**Expected behavior**:
- Very stable temperature (±0.2°C)
- More frequent but smooth adjustments
- Learning will likely set limit_timeout around 8-10 minutes

---

### Example 4: Manual Tuning (No Learning)

**Setup**:
- Prefer manual control
- Want to fine-tune yourself

**Steps**:
1. Start with defaults
2. Disable learning: `switch.smart_fan_learning_enabled` → Off
3. Monitor for a day
4. Adjust parameters based on `sensor.smart_fan_status`
5. Iterate until satisfied

**Tuning guide**:
- Too many changes? → Increase `deadband` or `min_interval`
- Temperature drifts? → Decrease `deadband` or `limit_timeout`
- Slow recovery? → Decrease `soft_error`
- Emergency mode too often? → Increase `hard_error`

---

### Example 5: Night Setback Optimization

**Setup**:
- Automatic night setback (21°C day, 18°C night)

**The integration automatically**:
- Detects setpoint drops
- Immediately reduces fan to minimum
- Avoids overcooling during night

**No special configuration needed!** The "Night Mode" logic is built-in.

---

## 📚 Additional Resources

### Related Projects
- [Versatile Thermostat](https://github.com/jmcollin78/versatile_thermostat) - Required companion integration

### Community
- Report issues: [GitHub Issues](https://github.com/Gamso/smart_fan_controller/issues)
- Discussions: [GitHub Discussions](https://github.com/Gamso/smart_fan_controller/discussions)

---

## 📄 License

This project is licensed under the MIT License.

---

**Enjoy smarter, more comfortable climate control!** 🌡️✨
