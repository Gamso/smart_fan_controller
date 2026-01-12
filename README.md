# Predictive Fan Controller for Versatile Thermostat

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

**Smart, adaptive, predictive fan speed control for Air Conditioning systems with continuous learning**.

Designed to work seamlessly with Versatile Thermostat for tighter temperature control, improved comfort, and reduced mechanical wear. Now featuring **adaptive learning** that automatically optimizes control parameters for your specific environment.


## Installation

### HACS (Recommended)

This card is available in HACS (Home Assistant Community Store).

Click the button below to add this repository to HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gamso&bouton&repository=smart_fan_controller&category=integration)



## ✨ Overview

Predictive Fan Controller is a custom Home Assistant component that dynamically adjusts AC fan speed based on thermal prediction rather than reactive temperature changes.<br>
By leveraging Versatile Thermostat's thermal slope and acceleration data, the controller anticipates temperature evolution and adapts fan output before overshoot or drift occurs.

### 🆕 Adaptive Learning (NEW!)

The controller now features an **adaptive learning system** that:
- **Learns your environment**: Automatically characterizes thermal inertia and system response times
- **Adapts fan mode effectiveness**: Tracks how each fan speed affects temperature in your specific setup
- **Self-optimizes parameters**: Reduces or eliminates the need for manual parameter tuning
- **Persists learning**: Saves learned data between restarts to continuously improve
- **Builds confidence**: Gradually transitions from static to learned parameters as confidence grows


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


## 🤖 Adaptive Learning System

The adaptive learning system observes system behavior and continuously improves control performance:

### Learning Process

1. **Observation**: Tracks temperature changes, fan mode activations, and thermal responses
2. **Fan Mode Profiling**: Learns the effectiveness of each fan speed (low, medium, high, turbo)
3. **Thermal Characterization**: Measures thermal inertia and system response times
4. **Parameter Adaptation**: Dynamically adjusts control thresholds based on learned characteristics
5. **Confidence Building**: Gradually trusts learned parameters as more data is collected

### Confidence Levels

- **Low (<0.3)**: Uses static parameters exclusively (safe initial mode)
- **Medium (0.3-0.7)**: Blends static and learned parameters proportionally
- **High (>0.7)**: Primarily uses learned parameters for optimal performance

### Learned Metrics

For each fan mode, the system tracks:
- Average slope change (thermal response strength)
- Average response time to reach target
- Effectiveness score (0-1 scale)
- Overshoot/undershoot event counts
- Temperature change rate

### Persistent Storage

Learning data is:
- Saved automatically every hour (configurable)
- Saved on Home Assistant shutdown
- Stored in JSON format for transparency
- Specific to each climate entity
- Versioned for future compatibility


## ⚙️ Configuration Parameters

### Static Parameters (Fallback Values)

These serve as initial values and fallback when learning confidence is low:

| Parameter                   | Default | Description                                        |
| --------------------------- | ------- | -------------------------------------------------- |
| `deadband`                  | 0.2°C   | Precision margin for the comfort zone              |
| `min_interval`              | 10 min  | Minimum duty cycle between fan speed transitions   |
| `soft_error`                | 0.3°C   | Trigger point for active thermal correction        |
| `hard_error`                | 0.6°C   | Safety threshold for maximum cooling/heating power |
| `projected_error_threshold` | 0.5°C   | Prediction sensitivity for proactive boosting      |

### Adaptive Learning Parameters (NEW!)

| Parameter                      | Default | Description                                           |
| ------------------------------ | ------- | ----------------------------------------------------- |
| `enable_adaptive_learning`     | `true`  | Enable/disable the adaptive learning system           |
| `learning_rate`                | 0.1     | Learning speed (0.05-0.2, higher = faster adaptation) |
| `learning_save_interval`       | 60 min  | How often to persist learning data to disk            |

### Dynamic Parameters (Computed by Learning)

When learning confidence is sufficient, these replace static parameters:
- **Adaptive deadband**: Based on learned thermal inertia
- **Adaptive soft_error**: Scaled with system responsiveness
- **Adaptive hard_error**: Tuned to prevent overshoot based on history
- **Adaptive min_interval**: Optimized for system response time
- **Adaptive projection threshold**: Calibrated to prediction accuracy


## �� Benefits of Adaptive Learning

1. **Zero-Configuration**: Works optimally without manual parameter tuning
2. **Environment-Specific**: Adapts to your room size, insulation, and HVAC capacity
3. **Seasonal Adaptation**: Automatically adjusts to changing thermal behavior
4. **Continuous Improvement**: Gets smarter over time with more observations
5. **Backward Compatible**: Existing installations work unchanged with learning disabled
6. **Transparent**: Learning data saved in human-readable JSON format


## 🚀 Getting Started with Adaptive Learning

1. **Install normally** via HACS
2. **Configure** your climate entity in the integration (adaptive learning enabled by default)
3. **Wait 24-48 hours** for initial learning (system operates normally during this period)
4. **Monitor confidence** via diagnostic sensors (optional)
5. **Enjoy** improved temperature control as the system learns!

### Tips for Optimal Learning

- Let the system run for at least a week for best results
- Avoid manual fan overrides during the first few days
- If you make major changes (new HVAC unit, insulation upgrade), consider resetting learning data


## 📊 Monitoring Learning Progress

The system exposes learning metrics that can be monitored through:
- Learning confidence level (0-1 scale)
- Total decisions made
- Successful predictions count
- Learned thermal inertia value
- Per-fan-mode effectiveness scores

*(Diagnostic sensors can be added in a future update)*


## 🔧 Advanced Configuration

### Disabling Adaptive Learning

If you prefer the original static parameter approach:

```yaml
# In config flow UI, set:
enable_adaptive_learning: false
```

### Adjusting Learning Speed

For faster adaptation (in rapidly changing environments):
```yaml
learning_rate: 0.15  # Default: 0.1, Range: 0.05-0.2
```

For slower, more conservative learning:
```yaml
learning_rate: 0.05
```

### Changing Save Frequency

To reduce writes to disk:
```yaml
learning_save_interval: 120  # Save every 2 hours instead of 1
```


## 🛠️ Technical Details

### Architecture

- **LearningStorage**: Manages persistent JSON storage with atomic writes
- **AdaptiveLearning**: Implements observation and parameter calculation logic  
- **SmartFanController**: Enhanced with adaptive parameter support
- **Integration**: Seamless with existing Home Assistant workflows

### Learning Data Location

Learning data is stored in:
```
<config>/smart_fan_controller/smart_fan_learning_<climate_entity_id>.json
```

### Safety & Fallback

- All learning is additive - the system never becomes less safe than static mode
- Hard safety limits (emergency mode) are never overridden by learning
- Corrupted learning files are automatically ignored with fallback to defaults
- Version checking ensures compatibility across updates


## 📝 Migration from Previous Version

Existing installations:
- Work without any changes required
- Can enable adaptive learning anytime via reconfiguration
- All existing parameters continue to function as fallback values


## 🐛 Troubleshooting

### Learning seems slow or not improving

- Ensure sufficient observation time (minimum 100 decisions needed)
- Check that HVAC mode doesn't change too frequently
- Verify Versatile Thermostat is providing slope data correctly

### Want to reset learning

Delete the learning JSON file:
```bash
rm <config>/smart_fan_controller/smart_fan_learning_*.json
```

Then restart Home Assistant.


## 📚 Further Reading

- Detailed specification: [.github/ADAPTIVE_LEARNING_ALGORITHM.md](.github/ADAPTIVE_LEARNING_ALGORITHM.md)
- Implementation details in source code comments
- Test suite in `tests/test_adaptive_learning.py` and `tests/test_learning_storage.py`
