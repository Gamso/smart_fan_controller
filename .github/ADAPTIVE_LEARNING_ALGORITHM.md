# Adaptive Learning Algorithm - Feature Enhancement

## Summary

Evolve the current predictive fan controller algorithm to a **generic, self-adaptive hybrid system** with continuous learning capabilities. The goal is to eliminate or minimize hardcoded static parameters and enable the controller to automatically learn and adapt to any thermal environment and thermal inertia characteristics.

## Problem Statement

The current algorithm relies on several static configuration parameters that require manual tuning for different environments:
- `soft_error` (default: 0.3°C)
- `hard_error` (default: 0.6°C)
- `projected_error_threshold` (default: 0.5°C)
- `min_interval` (default: 10 minutes)
- `deadband` (default: 0.2°C)

These static values work well in controlled environments but may not be optimal for:
- Different room sizes and thermal masses
- Varying insulation qualities
- Different HVAC system capacities
- Seasonal changes in thermal behavior
- Different fan mode effectiveness

## Proposed Solution

### 1. Hybrid Adaptive Learning Architecture

Implement a **continuous learning system** that combines:

#### A. Real-time Adaptation
- Monitor temperature response to fan speed changes
- Dynamically adjust thresholds based on observed system behavior
- Use exponential moving averages for smooth parameter adaptation

#### B. Fan Mode Dynamics Learning
- **Learn the thermal impact of each fan mode** (low, medium, high, turbo)
- Track for each fan mode:
  - Average temperature slope change after activation
  - Time to reach target temperature
  - Overshoot/undershoot patterns
  - Energy efficiency metrics
  
#### C. Thermal Inertia Characterization
- Measure system response time (time constant)
- Learn optimal prediction horizons
- Adapt min_interval based on system responsiveness

### 2. Persistent Learning Storage

Implement a **learning state persistence mechanism**:

```python
learning_state = {
    "fan_mode_profiles": {
        "low": {
            "avg_slope_change": float,
            "avg_response_time": float,
            "effectiveness_score": float,
            "activation_count": int,
            "last_updated": timestamp
        },
        # ... for each fan mode
    },
    "thermal_parameters": {
        "learned_thermal_inertia": float,
        "optimal_prediction_window": float,
        "adaptive_soft_error": float,
        "adaptive_hard_error": float,
        "adaptive_deadband": float
    },
    "environment_metrics": {
        "avg_ambient_change_rate": float,
        "typical_load_duration": float
    },
    "learning_metadata": {
        "total_decisions": int,
        "successful_predictions": int,
        "learning_confidence": float,
        "last_saved": timestamp
    }
}
```

**Storage Strategy:**
- Save learning state to a JSON file in Home Assistant's data directory
- Auto-save periodically (e.g., every hour) and on shutdown
- Load on startup to resume learning from previous state
- Implement versioning for future compatibility

### 3. Adaptive Parameter Calculation

Replace static parameters with dynamic calculations:

#### Dynamic Soft/Hard Error Thresholds
```python
def calculate_adaptive_thresholds(self):
    """
    Adjust error thresholds based on:
    - Historical prediction accuracy
    - System thermal inertia
    - Fan mode effectiveness variance
    """
    base_threshold = self.learned_thermal_inertia * 0.5
    
    self.adaptive_soft_error = base_threshold * self.confidence_factor
    self.adaptive_hard_error = base_threshold * 2.0 * self.confidence_factor
```

#### Dynamic Min Interval
```python
def calculate_adaptive_interval(self):
    """
    Adjust minimum interval based on:
    - System response time
    - Recent prediction accuracy
    - Current thermal stability
    """
    base_interval = self.learned_response_time * 1.5
    
    # Increase interval if system is stable
    # Decrease if predictions are consistently accurate
    self.adaptive_min_interval = base_interval * self.stability_factor
```

#### Dynamic Projected Error Threshold
```python
def calculate_adaptive_projection_threshold(self):
    """
    Adjust projection sensitivity based on:
    - Prediction accuracy history
    - Thermal acceleration patterns
    """
    self.adaptive_projection_threshold = (
        self.learned_thermal_inertia * 
        self.avg_prediction_error_factor
    )
```

### 4. Learning Algorithm

Implement continuous learning through observation:

```python
def update_learning_from_observation(
    self, 
    fan_mode_before: str,
    fan_mode_after: str,
    temp_before: float,
    temp_after: float,
    time_elapsed: float,
    slope_before: float,
    slope_after: float
):
    """
    Update learning profiles based on observed system behavior
    """
    # 1. Calculate actual thermal response
    actual_temp_change = temp_after - temp_before
    actual_slope_change = slope_after - slope_before
    
    # 2. Update fan mode profile with exponential moving average
    profile = self.learning_state["fan_mode_profiles"][fan_mode_after]
    alpha = 0.1  # Learning rate
    
    profile["avg_slope_change"] = (
        alpha * actual_slope_change + 
        (1 - alpha) * profile["avg_slope_change"]
    )
    
    profile["activation_count"] += 1
    profile["last_updated"] = time.time()
    
    # 3. Update thermal inertia estimate
    self._update_thermal_inertia(time_elapsed, actual_temp_change)
    
    # 4. Recalculate adaptive parameters
    self._recalculate_adaptive_parameters()
    
    # 5. Schedule save of learning state
    self._schedule_learning_state_save()
```

### 5. Confidence-Based Decision Making

Implement a confidence metric that influences decision aggressiveness:

```python
def calculate_learning_confidence(self):
    """
    Calculate confidence level based on:
    - Number of observations collected
    - Prediction accuracy trend
    - Environment stability
    """
    min_observations = 100
    observation_confidence = min(
        1.0, 
        self.total_decisions / min_observations
    )
    
    accuracy_confidence = (
        self.successful_predictions / 
        max(1, self.total_decisions)
    )
    
    return (observation_confidence * 0.5 + accuracy_confidence * 0.5)
```

Use confidence to modulate behavior:
- **Low confidence** (<0.3): Conservative decisions, use fallback static parameters
- **Medium confidence** (0.3-0.7): Blend static and learned parameters
- **High confidence** (>0.7): Fully rely on learned parameters

### 6. Fallback and Safety Mechanisms

Ensure robustness:

1. **Fallback to Static Parameters:**
   - When learning confidence is low
   - When learning state is corrupted or missing
   - During first-time setup

2. **Anomaly Detection:**
   - Detect unusual thermal behavior
   - Reset learning for specific fan modes if patterns change dramatically
   - Flag potential sensor failures

3. **Learning Reset:**
   - Provide option to reset learning (e.g., after HVAC maintenance)
   - Partial reset for specific fan modes
   - Full system reset

## Implementation Plan

### Phase 1: Foundation (Weeks 1-2)
- [ ] Design learning state data structure
- [ ] Implement persistent storage (JSON serialization)
- [ ] Add learning state load/save on startup/shutdown
- [ ] Create unit tests for storage mechanism

### Phase 2: Observation System (Weeks 3-4)
- [ ] Implement observation logging system
- [ ] Add fan mode dynamics tracking
- [ ] Implement thermal inertia calculation
- [ ] Create tests for observation accuracy

### Phase 3: Adaptive Parameters (Weeks 5-6)
- [ ] Implement dynamic threshold calculations
- [ ] Add confidence-based parameter blending
- [ ] Integrate adaptive parameters into decision logic
- [ ] Create tests for parameter adaptation

### Phase 4: Integration & Testing (Weeks 7-8)
- [ ] Integrate learning system with existing controller
- [ ] Add configuration UI for learning management
- [ ] Comprehensive integration testing
- [ ] Field testing in multiple environments

### Phase 5: Refinement (Weeks 9-10)
- [ ] Performance optimization
- [ ] Add telemetry/debugging sensors
- [ ] Documentation updates
- [ ] Migration guide for existing installations

## Benefits

1. **Reduced Configuration Burden:** Users no longer need to tune parameters manually
2. **Better Performance:** System adapts to specific environment characteristics
3. **Seasonal Adaptation:** Automatically adjusts to changing thermal behavior
4. **Portability:** Same configuration works across different installations
5. **Continuous Improvement:** System gets smarter over time
6. **Robustness:** Adapts to changes in HVAC system or building modifications

## Backward Compatibility

- Keep static parameters as fallback and initial values
- Provide migration path for existing installations
- Allow users to disable learning if desired
- Maintain existing configuration flow schema

## Configuration Options (New)

Add to config flow:
- `enable_adaptive_learning` (boolean, default: true)
- `learning_rate` (float, 0.05-0.2, default: 0.1)
- `learning_persistence_interval` (int, minutes, default: 60)
- `learning_reset` (button to trigger reset)

## Success Metrics

- Reduction in temperature overshoot/undershoot events
- Faster convergence to target temperature
- Reduced fan speed changes (less wear)
- User satisfaction with reduced manual tuning

## Technical Considerations

- **Storage Location:** Use Home Assistant's config directory
- **File Format:** JSON for human-readability and debugging
- **Size Management:** Implement periodic cleanup of old learning data
- **Thread Safety:** Ensure learning state updates are thread-safe
- **Performance:** Minimize impact on control loop execution time

## References

- Current algorithm documentation in README.md
- Existing controller implementation in `controller.py`
- Test cases in `tests/` directory

## Questions for Discussion

1. Should we expose learning metrics as Home Assistant sensors for transparency?
2. What should be the default learning rate for new installations?
3. Should we implement a "learning mode" that accelerates initial learning?
4. How should we handle multiple climate zones with shared fan controller?
5. Should learning be per-HVAC mode (heat/cool) or unified?

---

**Issue Type:** Feature Enhancement  
**Priority:** Medium  
**Complexity:** High  
**Estimated Effort:** 8-10 weeks  
**Requires:** Backward compatibility testing, comprehensive field testing
