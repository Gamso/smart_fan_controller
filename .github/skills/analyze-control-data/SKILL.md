---
name: analyze-control-data
description: "Analyze Smart Fan Controller CSV data files to diagnose algorithm behavior, identify missed MPC opportunities, detect defrost artifacts, and propose improvements. Use when: the user provides a CSV data file; asking to analyze overnight or morning behavior; asking why the fan did or did not change; evaluating MPC decision quality; diagnosing defrost or setpoint-drop events."
argument-hint: "CSV file path or description of the behavior to diagnose"
---

# Analyze Control Data

## When to Use

- User provides a `smart_fan_controller_data_*.csv` file (from `data/`)
- User describes unexpected behavior ("fan stayed on med all night", "slow morning rise")
- User wants to evaluate MPC decision quality
- Diagnosing defrost artifacts, setpoint drops, or window-open disturbances

## Column Reference

| Column | Meaning |
|--------|---------|
| `timestamp` | ISO datetime of each control cycle (~2 min intervals) |
| `hvac_mode` | `heat` or `cool` |
| `current_temp` | Sensor temperature (°C) |
| `target_temp` | Active setpoint (°C) |
| `vtherm_slope` | EMA-smoothed slope from VTherm (°C/h) |
| `error` | Signed error (positive = needs action) |
| `current_fan` | Fan mode at start of cycle |
| `fan_mode` | Fan mode selected by controller |
| `reason` | MPC decision reason string |
| `phase` | `DEAD_TIME` / `TRANSIENT` / `ESTABLISHED` |
| `effective_slope` | Slope actually used for decisions |
| `is_window_open` | `True`/`False` |
| `force` | `True` if timer was bypassed |
| `mpc_status` | `Ready` / `Not ready` / `Setpoint drop` / `Disturbed` / `Idle` / `Low confidence` |
| `mpc_fan` | Fan mode chosen by MPC |
| `mpc_would_change` | `yes` / `no` |
| `mpc_cost` | MPC optimization cost |
| `mpc_confidence` | Profile coverage % |
| `mpc_temp_10m` | MPC 10-minute temperature prediction |
| `mpc_temp_30m` | MPC 30-minute temperature prediction |
| `defrost_active` | `True` when defrost protection is active |
| `learning_ready` | `True` when ≥10 samples per profile |

## Analysis Procedure

### 1. Load and Inspect
- Read the CSV with pandas or csv module
- Check time range, number of rows, unique HVAC modes
- Identify gaps (missing cycles, restarts)

### 2. Key Diagnostic Checks

**Setpoint drop events (`reason` contains "Setpoint drop")**
- Check `target_temp` drop magnitude
- Verify `fan_mode` went to lowest mode
- Check MPC also reported "Setpoint drop"

**Defrost events (`defrost_active == True`)**
- Find the slope drop that triggered detection: look for `vtherm_slope` crossing sharply negative
- Verify MPC paused (status should be "Disturbed")
- Check duration: 20-min cooldown from detection

**MPC disturbed periods (`mpc_status == "Disturbed"`)**
- Identify what triggered the disturbance: defrost, window open, HVAC idle
- Check `mpc_confidence` — low confidence = not enough learning data
- Check `mpc_known_profiles` — MPC needs ≥10 samples per mode

**Slow temperature recovery**
- Plot `current_temp` vs `target_temp` over time
- Look for unnecessary step-downs (reason contains "Hysteresis" or step-down hold)
- Cross-check with `defrost_active` — was defrost correctly detected?

**Night setpoint drop mismatch (MPC on "med" instead of "silent")**
- Filter to period where `target_temp` dropped significantly
- Check `mpc_status` — should be "Setpoint drop" with lowest fan
- If not, check whether defrost or idle paused the MPC

### 3. Report Format

Provide a narrative structured as:
1. **Period summary**: time range, hvac mode, temperature trajectory
2. **Key events**: defrost cycles, setpoint drops, fan changes — with timestamps
3. **Algorithm assessment**: MPC decisions triggered correctly? any missed steps?
4. **MPC quality**: confidence levels, key disturbance periods and their cause
5. **Recommendations**: concrete parameter changes or code fixes if warranted

## Code Hints

- Load CSV: `pd.read_csv(path, parse_dates=["timestamp"])`
- Filter defrost: `df[df["defrost_active"] == True]`
- Find mode changes: `df[df["current_fan"] != df["fan_mode"]]`
- MPC disturbance: `df[df["mpc_status"] == "Disturbed"]`
- Reason summary: `df["reason"].value_counts()`
