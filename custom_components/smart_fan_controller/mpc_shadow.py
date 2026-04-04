"""Shadow-mode learned model + MPC-lite scaffold."""
from __future__ import annotations

from dataclasses import dataclass
import logging

from .const import (
    DEAD_TIME_SAFETY_FACTOR,
    DEFAULT_DEAD_TIME,
    DELTA_TIME_CONTROL_LOOP,
    PHASE_DEAD_TIME,
    PHASE_ESTABLISHED,
    PHASE_TRANSIENT,
    THRESHOLD_TARGET_DROP,
)
from .thermal_learning import ThermalLearning

_LOGGER = logging.getLogger(__name__)

MIN_INTERVAL_CHANGE_PENALTY = 25.0
DISTURBANCE_EMA_ALPHA = 0.2
DISTURBANCE_DECAY = 0.85
MAX_DISTURBANCE_BIAS = 2.0
BASE_SWITCH_GAIN_MARGIN = 0.1
NEAR_TARGET_SWITCH_GAIN_MARGIN = 0.3
APPROACHING_TARGET_SWITCH_GAIN_MARGIN = 0.15
PHASE_SWITCH_MARGIN_BONUS = 0.1
STEP_SWITCH_MARGIN = 0.05
UNDER_TARGET_STEPDOWN_GAIN_MARGIN = 0.2
UNDER_TARGET_STEPDOWN_GAIN_PER_DEG = 0.5
UNDER_TARGET_SHORTFALL_RESERVE = 0.1
FLOOR_VIOLATION_LINEAR_WEIGHT = 12.0
FLOOR_VIOLATION_QUADRATIC_WEIGHT = 30.0


@dataclass(slots=True)
class ModeSimulation:
    """One candidate fan-mode simulation over the MPC horizon."""

    fan_mode: str
    total_cost: float
    predicted_temp_10m: float
    predicted_temp_30m: float
    known_profile: bool


class MPCShadowController:
    """Background-only learned model + MPC-lite scaffold."""

    def __init__(
        self,
        *,
        learning: ThermalLearning,
        deadband: float,
        min_interval: int,
        fan_modes: list[str] | None = None,
        enabled: bool = False,
        horizon_minutes: int = 30,
        cycle_minutes: int = DELTA_TIME_CONTROL_LOOP,
    ) -> None:
        self._learning = learning
        self._deadband = deadband
        self._min_interval = min_interval
        self._fan_modes = fan_modes
        self._enabled = enabled
        self._horizon_minutes = horizon_minutes
        self._cycle_minutes = cycle_minutes
        self._disturbance_bias = 0.0

    @property
    def enabled(self) -> bool:
        """Return whether shadow mode is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable shadow mode."""
        self._enabled = value

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the currently known fan modes."""
        return self._fan_modes

    @fan_modes.setter
    def fan_modes(self, modes: list[str] | None) -> None:
        """Update the available fan modes."""
        self._fan_modes = modes

    def evaluate(
        self,
        *,
        current_temp: float,
        target_temp: float,
        vtherm_slope: float,
        hvac_mode: str,
        current_fan: str | None,
        live_decision_fan: str | None,
        is_window_open: bool = False,
        is_defrost_active: bool = False,
        minutes_since_change: float = 0.0,
    ) -> dict:
        """Evaluate the best shadow fan mode for the current cycle."""
        _LOGGER.debug(
            "Shadow evaluate: enabled=%s hvac=%s current_temp=%.2f target=%.2f slope=%.3f current_fan=%s live_decision=%s minutes_since_change=%.1f window_open=%s",
            self._enabled,
            hvac_mode,
            current_temp,
            target_temp,
            vtherm_slope,
            current_fan,
            live_decision_fan,
            minutes_since_change,
            is_window_open,
        )

        if not self._enabled:
            return self._payload(
                status="Disabled",
                fan_mode=current_fan,
                reason="Shadow mode disabled",
                matches_live="disabled",
                would_change_now="no",
            )

        if hvac_mode in ("off", "dry", "fan_only"):
            return self._payload(
                status="Idle",
                fan_mode=current_fan,
                reason=f"HVAC mode '{hvac_mode}' is not simulated",
                matches_live="n/a",
                would_change_now="no",
            )

        fan_modes = self._fan_modes or ([] if current_fan is None else [current_fan])
        if not fan_modes:
            return self._payload(
                status="Unavailable",
                fan_mode=current_fan,
                reason="No fan modes available yet",
                matches_live="n/a",
                would_change_now="no",
            )

        active_fan = current_fan if current_fan in fan_modes else fan_modes[0]
        current_effective_slope = -vtherm_slope if hvac_mode == "cool" else vtherm_slope
        dead_time = self._learning.get_dead_time()
        change_allowed = minutes_since_change >= self._min_interval
        phase = self._detect_phase(minutes_since_change, dead_time)
        current_mode_slope, current_known_profile = self._get_mode_slope(
            active_fan,
            hvac_mode,
            active_fan,
            current_effective_slope,
            fan_modes,
        )
        self._update_disturbance_bias(
            observed_effective_slope=current_effective_slope,
            expected_effective_slope=current_mode_slope,
            known_profile=current_known_profile,
            phase=phase,
            is_window_open=is_window_open,
            is_defrost_active=is_defrost_active,
        )

        if is_window_open:
            return self._payload(
                status="Disturbed",
                fan_mode=active_fan,
                reason="Window open detected: shadow model paused",
                matches_live="n/a",
                would_change_now="no",
                dead_time=dead_time,
                disturbance_bias=self._disturbance_bias,
            )

        if is_defrost_active:
            return self._payload(
                status="Disturbed",
                fan_mode=active_fan,
                reason="Defrost active: shadow model paused",
                matches_live="n/a",
                would_change_now="no",
                dead_time=dead_time,
                disturbance_bias=self._disturbance_bias,
            )

        # Setpoint drop: mirror the live controller's immediate minimum-speed rule.
        # When the target moves far away (e.g. night setpoint), there is no point
        # running the full MPC cost optimisation — the answer is always the lowest mode.
        current_error = self._temperature_error(current_temp, target_temp, hvac_mode)
        if current_error < THRESHOLD_TARGET_DROP:
            lowest_fan = fan_modes[0]
            matches_live = "n/a" if live_decision_fan is None else ("yes" if live_decision_fan == lowest_fan else "no")
            would_change = "yes" if active_fan != lowest_fan else "no"
            return self._payload(
                status="Setpoint drop",
                fan_mode=lowest_fan,
                reason=f"Setpoint drop: target moved away ({current_error:.1f}°C), minimum speed",
                matches_live=matches_live,
                would_change_now=would_change,
                dead_time=dead_time,
                disturbance_bias=self._disturbance_bias,
            )

        simulations: list[ModeSimulation] = []
        known_profiles = 0
        current_index = fan_modes.index(active_fan)

        for fan_mode in fan_modes:
            mode_slope, known_profile = self._get_mode_slope(
                fan_mode,
                hvac_mode,
                active_fan,
                current_effective_slope,
                fan_modes,
            )
            sim = self._simulate_mode(
                current_temp=current_temp,
                target_temp=target_temp,
                hvac_mode=hvac_mode,
                current_fan=active_fan,
                candidate_fan=fan_mode,
                current_effective_slope=current_effective_slope,
                candidate_mode_slope=mode_slope,
                dead_time=dead_time,
                candidate_index=fan_modes.index(fan_mode),
                current_index=current_index,
                change_allowed=change_allowed,
                known_profile=known_profile,
            )
            simulations.append(sim)
            known_profiles += int(known_profile)

        current_simulation = next(sim for sim in simulations if sim.fan_mode == active_fan)
        best = min(simulations, key=lambda item: item.total_cost)
        selection_note = ""

        if not change_allowed and best.fan_mode != active_fan:
            selection_note = f"Min interval holds {active_fan} until a change is allowed"
            best = current_simulation
        elif change_allowed and best.fan_mode != active_fan:
            best_index = fan_modes.index(best.fan_mode)
            required_gain = self._required_switch_gain(
                current_error=current_error,
                phase=phase,
                candidate_index=best_index,
                current_index=current_index,
            )
            actual_gain = current_simulation.total_cost - best.total_cost
            if actual_gain < required_gain:
                selection_note = (
                    f"Hysteresis holds {active_fan}: {best.fan_mode} only improves by "
                    f"{actual_gain:.2f} < {required_gain:.2f}"
                )
                best = current_simulation
            else:
                hold_note = self._step_down_hold_note(
                    candidate=best,
                    active_fan=active_fan,
                    hvac_mode=hvac_mode,
                    target_temp=target_temp,
                    current_error=current_error,
                    candidate_index=best_index,
                    current_index=current_index,
                    phase=phase,
                )
                if hold_note:
                    selection_note = hold_note
                    best = current_simulation

        if change_allowed and best.fan_mode != active_fan:
            limited_best = self._apply_step_down_limit(
                best=best,
                active_fan=active_fan,
                simulations=simulations,
                fan_modes=fan_modes,
            )
            if limited_best.fan_mode != best.fan_mode:
                if selection_note:
                    selection_note += " | "
                selection_note += f"Step-down limited to {limited_best.fan_mode}"
                best = limited_best

        confidence = self._compute_confidence(known_profiles, len(fan_modes), phase)
        matches_live = "n/a" if live_decision_fan is None else ("yes" if live_decision_fan == best.fan_mode else "no")
        would_change_now = "yes" if change_allowed and best.fan_mode != active_fan else "no"
        status = "Ready" if confidence >= 0.5 else "Low confidence"
        _LOGGER.debug(
            "Shadow candidates: %s",
            [
                (
                    sim.fan_mode,
                    round(sim.total_cost, 3),
                    round(sim.predicted_temp_10m, 2),
                    round(sim.predicted_temp_30m, 2),
                    sim.known_profile,
                )
                for sim in simulations
            ],
        )
        reason = (
            f"Shadow recommends {best.fan_mode}: cost={best.total_cost:.2f}, "
            f"T+10={best.predicted_temp_10m:.2f}C, T+30={best.predicted_temp_30m:.2f}C"
        )
        if selection_note:
            reason += f" | {selection_note}"
        if abs(self._disturbance_bias) >= 0.05:
            reason += f" | Bias={self._disturbance_bias:+.2f}C/h"
        if not change_allowed:
            reason += (
                f" | Min interval active ({minutes_since_change:.1f}/"
                f"{self._min_interval:.1f} min)"
            )

        _LOGGER.debug(
            "Shadow result: active=%s best=%s status=%s confidence=%.2f known_profiles=%d/%d bias=%.3f note=%s",
            active_fan,
            best.fan_mode,
            status,
            confidence,
            known_profiles,
            len(fan_modes),
            self._disturbance_bias,
            selection_note or "none",
        )

        return self._payload(
            status=status,
            fan_mode=best.fan_mode,
            reason=reason,
            predicted_10m=best.predicted_temp_10m,
            predicted_30m=best.predicted_temp_30m,
            cost=best.total_cost,
            confidence=confidence * 100.0,
            matches_live=matches_live,
            would_change_now=would_change_now,
            dead_time=dead_time,
            known_profiles=known_profiles,
            disturbance_bias=self._disturbance_bias,
        )

    def _get_mode_slope(
        self,
        fan_mode: str,
        hvac_mode: str,
        current_fan: str,
        current_effective_slope: float,
        fan_modes: list[str],
    ) -> tuple[float, bool]:
        """Return the effective slope estimate for a candidate fan mode."""
        learned = self._learning.get_mode_effective_slope(fan_mode, hvac_mode)
        if learned is not None:
            _LOGGER.debug(
                "Shadow slope model: using learned profile for %s/%s = %.3f",
                hvac_mode,
                fan_mode,
                learned,
            )
            return learned, True

        baseline_slope = max(current_effective_slope, 0.2)
        current_rank = fan_modes.index(current_fan) + 1
        candidate_rank = fan_modes.index(fan_mode) + 1
        scaled = baseline_slope * (candidate_rank / max(current_rank, 1))
        _LOGGER.debug(
            "Shadow slope model: using fallback for %s/%s = %.3f (baseline=%.3f current_fan=%s)",
            hvac_mode,
            fan_mode,
            scaled,
            baseline_slope,
            current_fan,
        )
        return scaled, False

    @staticmethod
    def _detect_phase(minutes_since_change: float, dead_time: float) -> str:
        """Classify the current prediction phase without relying on the live controller."""
        effective_dead_time = max(dead_time, DEFAULT_DEAD_TIME if dead_time <= 0 else dead_time)
        if minutes_since_change < effective_dead_time:
            return PHASE_DEAD_TIME
        if minutes_since_change < effective_dead_time * DEAD_TIME_SAFETY_FACTOR:
            return PHASE_TRANSIENT
        return PHASE_ESTABLISHED

    def _update_disturbance_bias(
        self,
        *,
        observed_effective_slope: float,
        expected_effective_slope: float,
        known_profile: bool,
        phase: str,
        is_window_open: bool,
        is_defrost_active: bool = False,
    ) -> None:
        """Track slow external disturbances such as solar gains or occupancy."""
        if is_window_open or is_defrost_active:
            self._disturbance_bias *= DISTURBANCE_DECAY
            _LOGGER.debug(
                "Shadow disturbance bias decayed to %.3f because %s",
                self._disturbance_bias,
                "window is open" if is_window_open else "defrost is active",
            )
            return

        if not known_profile or phase != PHASE_ESTABLISHED:
            self._disturbance_bias *= DISTURBANCE_DECAY
            _LOGGER.debug(
                "Shadow disturbance bias decayed to %.3f because known_profile=%s phase=%s",
                self._disturbance_bias,
                known_profile,
                phase,
            )
            return

        residual = observed_effective_slope - expected_effective_slope
        updated = ((1 - DISTURBANCE_EMA_ALPHA) * self._disturbance_bias) + (DISTURBANCE_EMA_ALPHA * residual)
        self._disturbance_bias = max(-MAX_DISTURBANCE_BIAS, min(MAX_DISTURBANCE_BIAS, updated))
        _LOGGER.debug(
            "Shadow disturbance bias updated to %.3f (observed=%.3f expected=%.3f residual=%.3f)",
            self._disturbance_bias,
            observed_effective_slope,
            expected_effective_slope,
            residual,
        )

    def _simulate_mode(
        self,
        *,
        current_temp: float,
        target_temp: float,
        hvac_mode: str,
        current_fan: str,
        candidate_fan: str,
        current_effective_slope: float,
        candidate_mode_slope: float,
        dead_time: float,
        candidate_index: int,
        current_index: int,
        change_allowed: bool,
        known_profile: bool,
    ) -> ModeSimulation:
        """Simulate one constant fan mode over the prediction horizon."""
        steps = max(1, int(self._horizon_minutes / self._cycle_minutes))
        step_hours = self._cycle_minutes / 60.0
        blend = 0.45
        shadow_temp = current_temp
        predicted_10m = None
        predicted_30m = None
        cost = 0.0
        thermal_power = current_effective_slope
        change_delay = 0.0 if candidate_fan == current_fan else dead_time
        candidate_effective_slope = candidate_mode_slope + self._disturbance_bias

        current_error = self._temperature_error(current_temp, target_temp, hvac_mode)
        urgency_weight = 1.0 + max(current_error - self._deadband, 0.0) * 2.0

        for step in range(1, steps + 1):
            elapsed = step * self._cycle_minutes
            if elapsed <= change_delay:
                target_effective_slope = current_effective_slope
            else:
                target_effective_slope = candidate_effective_slope

            thermal_power += blend * (target_effective_slope - thermal_power)
            raw_slope = -thermal_power if hvac_mode == "cool" else thermal_power
            shadow_temp += step_hours * raw_slope

            if elapsed >= 10 and predicted_10m is None:
                predicted_10m = shadow_temp
            if elapsed >= 30 and predicted_30m is None:
                predicted_30m = shadow_temp

            error = self._temperature_error(shadow_temp, target_temp, hvac_mode)
            comfort_error = max(abs(error) - self._deadband, 0.0)
            overshoot = max(-error, 0.0)
            floor_violation = max(target_temp - shadow_temp, 0.0) if hvac_mode == "heat" else max(shadow_temp - target_temp, 0.0)
            cost += comfort_error * urgency_weight
            cost += 3.0 * overshoot * overshoot
            cost += FLOOR_VIOLATION_LINEAR_WEIGHT * floor_violation * urgency_weight
            cost += FLOOR_VIOLATION_QUADRATIC_WEIGHT * floor_violation * floor_violation

        cost += 0.15 * abs(candidate_index - current_index)
        cost += 0.05 * (candidate_index + 1)
        if candidate_fan != current_fan and not change_allowed:
            cost += MIN_INTERVAL_CHANGE_PENALTY

        return ModeSimulation(
            fan_mode=candidate_fan,
            total_cost=cost,
            predicted_temp_10m=current_temp if predicted_10m is None else predicted_10m,
            predicted_temp_30m=current_temp if predicted_30m is None else predicted_30m,
            known_profile=known_profile,
        )

    def _required_switch_gain(
        self,
        *,
        current_error: float,
        phase: str,
        candidate_index: int,
        current_index: int,
    ) -> float:
        """Return the minimum cost gain required before switching fan mode."""
        if current_error < -self._deadband:
            margin = BASE_SWITCH_GAIN_MARGIN
        elif current_error > (self._deadband * 2):
            margin = BASE_SWITCH_GAIN_MARGIN
        elif current_error > self._deadband:
            margin = APPROACHING_TARGET_SWITCH_GAIN_MARGIN
        else:
            margin = NEAR_TARGET_SWITCH_GAIN_MARGIN

        if phase != PHASE_ESTABLISHED:
            margin += PHASE_SWITCH_MARGIN_BONUS

        margin += STEP_SWITCH_MARGIN * abs(candidate_index - current_index)

        if candidate_index < current_index and current_error > 0:
            margin += UNDER_TARGET_STEPDOWN_GAIN_MARGIN
            margin += UNDER_TARGET_STEPDOWN_GAIN_PER_DEG * current_error

        return margin

    def _step_down_hold_note(
        self,
        *,
        candidate: ModeSimulation,
        active_fan: str,
        hvac_mode: str,
        target_temp: float,
        current_error: float,
        candidate_index: int,
        current_index: int,
        phase: str,
    ) -> str | None:
        """Return a note when a downward switch should be held despite lower cost."""
        if candidate_index >= current_index or current_error <= 0:
            return None

        if phase != PHASE_ESTABLISHED:
            return f"Below target: holding {active_fan} until the current response is established"

        predicted_error_10m = self._temperature_error(candidate.predicted_temp_10m, target_temp, hvac_mode)
        reserve = max(self._deadband * 0.5, UNDER_TARGET_SHORTFALL_RESERVE)
        if predicted_error_10m > reserve:
            return (
                f"Below target: holding {active_fan} because {candidate.fan_mode} still leaves "
                f"{predicted_error_10m:.2f}C shortfall at 10 min"
            )

        return None

    @staticmethod
    def _apply_step_down_limit(
        *,
        best: ModeSimulation,
        active_fan: str,
        simulations: list[ModeSimulation],
        fan_modes: list[str],
    ) -> ModeSimulation:
        """Allow at most one downward fan step per cycle, like the live controller.

        Upward moves are unrestricted so the MPC can ramp up aggressively.
        """
        current_index = fan_modes.index(active_fan)
        best_index = fan_modes.index(best.fan_mode)

        # Upward moves: no limit
        if best_index >= current_index:
            return best

        # Downward moves: limit to one step
        if best_index >= current_index - 1:
            return best

        limited_fan = fan_modes[current_index - 1]
        return next(sim for sim in simulations if sim.fan_mode == limited_fan)

    def _compute_confidence(self, known_profiles: int, total_profiles: int, phase: str) -> float:
        """Return a coarse confidence score for the current recommendation."""
        coverage = known_profiles / max(total_profiles, 1)
        base = 1.0 if self._learning.is_ready() else 0.45
        phase_factor = 1.0 if phase == PHASE_ESTABLISHED else 0.85
        disturbance_penalty = min(abs(self._disturbance_bias) / MAX_DISTURBANCE_BIAS, 0.35)
        return max(0.1, min(1.0, (base * (0.5 + 0.5 * coverage) * phase_factor) - disturbance_penalty))

    def _payload(
        self,
        *,
        status: str,
        fan_mode: str | None,
        reason: str,
        predicted_10m: float | None = None,
        predicted_30m: float | None = None,
        cost: float | None = None,
        confidence: float | None = None,
        matches_live: str = "n/a",
        would_change_now: str = "no",
        dead_time: float | None = None,
        known_profiles: int = 0,
        disturbance_bias: float | None = None,
    ) -> dict:
        """Build the shadow payload injected into sensors and CSV logs."""
        return {
            "mpc_shadow_status": status,
            "mpc_shadow_fan_mode": fan_mode,
            "mpc_shadow_reason": reason,
            "mpc_shadow_predicted_temperature_10m": round(predicted_10m, 2) if predicted_10m is not None else None,
            "mpc_shadow_predicted_temperature_30m": round(predicted_30m, 2) if predicted_30m is not None else None,
            "mpc_shadow_cost": round(cost, 3) if cost is not None else None,
            "mpc_shadow_confidence": round(confidence, 1) if confidence is not None else None,
            "mpc_shadow_matches_live": matches_live,
            "mpc_shadow_would_change_now": would_change_now,
            "mpc_shadow_dead_time": round(dead_time, 2) if dead_time is not None else None,
            "mpc_shadow_known_profiles": known_profiles,
            "mpc_shadow_disturbance_bias": round(disturbance_bias, 3) if disturbance_bias is not None else None,
        }

    @staticmethod
    def _temperature_error(temp: float, target_temp: float, hvac_mode: str) -> float:
        """Return the signed comfort error aligned with the active HVAC mode."""
        return (temp - target_temp) if hvac_mode == "cool" else (target_temp - temp)
