---
name: analyze-control-data
description: "Analyze Smart Fan Controller CSV data files to diagnose algorithm behavior, identify missed MPC opportunities, detect defrost artifacts, and propose improvements. Use when: the user provides a CSV data file; asking to analyze overnight or morning behavior; asking why the fan did or did not change; evaluating MPC shadow accuracy; diagnosing defrost or setpoint-drop events."
argument-hint: "CSV file path or description of the behavior to diagnose"
---

# Analyze Control Data

## When to Use

- User provides a `smart_fan_controller_data_*.csv` file (from `data/`)
- User describes unexpected behavior ("fan stayed on med all night", "slow morning rise")
- User wants to compare MPC shadow decisions vs live decisions
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
| `reason` | Zone decision reason string |
| `phase` | `DEAD_TIME` / `TRANSIENT` / `ESTABLISHED` |
| `effective_slope` | Slope actually used for decisions |
| `is_window_open` | `True`/`False` |
| `force` | `True` if timer was bypassed |
| `mpc_shadow_fan_mode` | Fan mode the shadow would choose |
| `mpc_shadow_status` | `Ready` / `Disabled` / `Setpoint drop` / `Disturbed` |
| `mpc_shadow_matches_live` | `yes` / `no` / `disabled` |
| `mpc_shadow_would_change` | `yes` / `no` |
| `mpc_shadow_cost` | MPC optimization cost |
| `mpc_shadow_confidence` | Profile coverage % |
| `mpc_shadow_reason` | Shadow decision explanation |
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
- Check MPC shadow also reported "Setpoint drop"

**Defrost events (`defrost_active == True`)**
- Find the slope drop that triggered detection: look for `vtherm_slope` crossing sharply negative
- Verify zones B/D were blocked (reason should contain "Defrost hold")
- Check duration: 20-min cooldown from detection to next step-down

**MPC divergence periods (`mpc_shadow_matches_live == "no"`)**
- Identify what shadow chose vs live
- Check `mpc_shadow_confidence` — low confidence = not enough learning data
- Check `mpc_shadow_known_profiles` — shadow needs ≥10 samples per mode

**Slow temperature recovery**
- Plot `current_temp` vs `target_temp` over time
- Look for unnecessary step-downs (reason "Maintenance: favorable slope" or "Braking")
- Cross-check with `defrost_active` — was defrost correctly detected?

**Night setpoint drop mismatch (MPC on "med" instead of "silent")**
- Filter to period where `target_temp` dropped significantly
- Check `mpc_shadow_status` — should be "Setpoint drop" with lowest fan
- If not, the snapshot was taken before the setpoint-drop fix was deployed

### 3. Report Format

Provide a narrative structured as:
1. **Period summary**: time range, hvac mode, temperature trajectory
2. **Key events**: defrost cycles, setpoint drops, fan changes — with timestamps
3. **Algorithm assessment**: zones triggered correctly? any missed steps?
4. **MPC shadow comparison**: agreement rate, key divergences and their cause
5. **Recommendations**: concrete parameter changes or code fixes if warranted

## Code Hints

- Load CSV: `pd.read_csv(path, parse_dates=["timestamp"])`
- Filter defrost: `df[df["defrost_active"] == True]`
- Find mode changes: `df[df["current_fan"] != df["fan_mode"]]`
- MPC divergence: `df[df["mpc_shadow_matches_live"] == "no"]`
- Zone summary: `df["reason"].value_counts()`
