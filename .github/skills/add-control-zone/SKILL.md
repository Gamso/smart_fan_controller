---
name: add-control-zone
description: "Add, modify, or remove a decision zone (A–F) in the SmartFanController decision engine. Use when: adding a new zone or sub-rule to controller.py; modifying zone conditions or actions; renaming or reordering zones; ensuring tests, README, and copilot-instructions.md stay in sync with zone changes."
argument-hint: "Zone letter and description of the new behavior (e.g. 'Zone D sub-rule: step down if slope favorable for 3+ cycles')"
---

# Add or Modify a Control Zone

## Decision Engine Overview

Zones are evaluated in strict priority order in `controller.py → calculate_decision()`.
Each zone has a dedicated `_decide_zone_X()` method. The first zone whose condition matches returns the decision.

Current order: **A → B → C → D → E → F**

## Procedure

### 1. Understand the Impact
- Read the existing zone method and its condition
- Check if the new condition overlaps with adjacent zones
- Confirm the action respects the ±1 step limit

### 2. Modify `controller.py`

**Zone condition change**: Edit `calculate_decision()` where the zone's `if` block lives.

**Zone action change**: Edit `_decide_zone_X()`.

**New sub-rule inside a zone**: Add a nested `if/elif` inside `_decide_zone_X()`.

**New zone**: Insert a new `if` block in `calculate_decision()` at the correct priority position and create `_decide_zone_X()`.

Constraints:
- Fan changes must be `±1` step — use `min(current_index + 1, max_index)` / `max(current_index - 1, 0)`
- `force=True` bypasses `min_interval`; use only for emergency/setpoint-drop
- Return `{"fan_mode": ..., "reason": ..., "force": ..., "zone": ...}` from the zone method
- Defrost protection zones (B, D step-down paths): verify defrost guard is still respected

### 3. Update `const.py` (if needed)
New threshold constants go in `const.py` with a `DEFAULT_` prefix and exposed in `config_flow.py` if user-tunable.

### 4. Write Tests
Create or extend `tests/test_heat.py` / `tests/test_cool.py` / a dedicated file.

Test template:
```python
# pylint: disable=protected-access,redefined-outer-name
"""Tests for Zone X: <description>."""

import pytest
from unittest.mock import patch
from custom_components.smart_fan_controller.controller import SmartFanController

@pytest.fixture
def controller():
    """Create a SmartFanController for zone tests."""
    return SmartFanController(
        fan_modes=["silent", "low", "med", "high", "superhigh"],
        deadband=0.2, min_interval=10, soft_error=0.3, hard_error=0.6,
    )

def test_zone_x_<condition>(controller):
    """<Zone letter>: <what this tests>."""
    with patch("time.time", return_value=3600.0):
        result = controller.calculate_decision(
            current_temp=..., target_temp=...,
            vtherm_slope=..., hvac_mode="heat", current_fan="med",
        )
    assert result["fan_mode"] == ...
    assert "<expected reason substring>" in result["reason"]
```

Run after editing: `python -m pytest tests/ -q`

### 5. Update Documentation

**`README.md`** — Decision Priority table in the Control Logic section.

**`.github/copilot-instructions.md`** — Decision Engine Zones table.

**`services.yaml`** (if the zone interacts with services).

### 6. Validate

```bash
python -m pytest tests/ -q          # all tests pass
./container hassfest                # manifest / translations valid
```
