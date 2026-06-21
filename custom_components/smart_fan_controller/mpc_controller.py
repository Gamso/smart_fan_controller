"""MPC controller: learned thermal model with cost-based fan selection."""
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

# Cost function weights (simulation loop in _simulate_mode)
COMFORT_ERROR_WEIGHT = 1.0
OVERSHOOT_QUADRATIC_WEIGHT = 3.0
FLOOR_VIOLATION_LINEAR_WEIGHT = 12.0
FLOOR_VIOLATION_QUADRATIC_WEIGHT = 30.0
MODE_CHANGE_DISTANCE_COST = 0.15
MODE_RANK_COST = 0.05
# Geometric growth of relative power draw per fan-mode rank. ~6**(1/3) so a
# 4-mode ladder reproduces the legacy [1.0, 1.5, 3.0, 6.0] power scaling while
# extending naturally to any number of modes.
MODE_POWER_RATIO = 1.82
MIN_INTERVAL_CHANGE_PENALTY = 25.0
URGENCY_SENSITIVITY = 2.0

# Disturbance bias tracker
DISTURBANCE_EMA_ALPHA = 0.2
DISTURBANCE_DECAY = 0.85
MAX_DISTURBANCE_BIAS = 2.0

# Hysteresis margins
BASE_SWITCH_GAIN_MARGIN = 0.1
NEAR_TARGET_SWITCH_GAIN_MARGIN = 0.3
APPROACHING_TARGET_SWITCH_GAIN_MARGIN = 0.15
PHASE_SWITCH_MARGIN_BONUS = 0.1
STEP_SWITCH_MARGIN = 0.05
UNDER_TARGET_STEPDOWN_GAIN_MARGIN = 0.2
UNDER_TARGET_STEPDOWN_GAIN_PER_DEG = 0.5
UNDER_TARGET_SHORTFALL_RESERVE = 0.1


@dataclass(slots=True)
class ModeSimulation:
    """One candidate fan-mode simulation over the MPC horizon."""

    fan_mode: str
    total_cost: float
    predicted_temp_10m: float
    predicted_temp_30m: float
    known_profile: bool


class MPCController:
    """Background-only learned model + MPC-lite scaffold."""

    def __init__(
        self,
        *,
        learning: ThermalLearning,
        deadband: float,
        min_interval: int,
        limit_timeout: int = 15,
        fan_modes: list[str] | None = None,
        horizon_minutes: int = 30,
        cycle_minutes: int = DELTA_TIME_CONTROL_LOOP,
    ) -> None:
        self._learning = learning
        self._deadband = deadband
        self._min_interval = min_interval
        self._limit_timeout = limit_timeout
        self._fan_modes = fan_modes
        self._horizon_minutes = horizon_minutes
        self._cycle_minutes = cycle_minutes
        self._disturbance_bias = 0.0

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the currently known fan modes."""
        return self._fan_modes

    @fan_modes.setter
    def fan_modes(self, modes: list[str] | None) -> None:
        """Update the available fan modes.

        Fan modes are assumed ordered weakest-to-strongest.  A warning is
        logged when learned profiles are available and their slopes violate
        this ordering, which usually indicates a configuration issue.
        """
        self._fan_modes = modes
        if modes and len(modes) >= 2:
            self._warn_if_unordered(modes)

    def _warn_if_unordered(self, modes: list[str]) -> None:
        """Log a warning when learned slopes don't match the assumed mode ordering."""
        prev_slope = None
        for mode in modes:
            slope = self._learning.get_mode_effective_slope(mode, "heat")
            if slope is None:
                return  # Not all profiles learned yet; skip check
            if prev_slope is not None and slope < prev_slope:
                _LOGGER.warning(
                    "Fan modes may not be ordered weakest-to-strongest: "
                    "learned slopes violate monotonicity at '%s'. "
                    "Check the fan_modes list in your climate entity",
                    mode,
                )
                return
            prev_slope = slope

    @property
    def learning(self) -> ThermalLearning:
        """Return the ThermalLearning instance used by this controller."""
        return self._learning

    @property
    def limit_timeout(self) -> int:
        """Return the configured static limit timeout (minutes)."""
        return self._limit_timeout

    def get_effective_timeout(self, hvac_mode: str = "unknown") -> float:
        """Return the adaptive timeout based on learned dead time, or static fallback."""
        if self._learning.is_ready():
            learned_dead_time = self._learning.get_dead_time(hvac_mode)
            return max(self._min_interval, learned_dead_time * DEAD_TIME_SAFETY_FACTOR)
        return self._limit_timeout

    @property
    def disturbance_bias(self) -> float:
        """Return the current disturbance bias estimate (°C/h)."""
        return self._disturbance_bias

    def evaluate(
        self,
        *,
        current_temp: float,
        target_temp: float,
        vtherm_slope: float,
        hvac_mode: str,
        current_fan: str | None,
        is_window_open: bool = False,
        is_defrost_active: bool = False,
        is_hvac_idle: bool = False,
        minutes_since_change: float = 0.0,
    ) -> dict:
        """Evaluate the best fan mode for the current cycle."""
        _LOGGER.debug(
            "MPC evaluate: hvac=%s current_temp=%.2f target=%.2f slope=%.3f current_fan=%s minutes_since_change=%.1f window_open=%s",
            hvac_mode,
            current_temp,
            target_temp,
            vtherm_slope,
            current_fan,
            minutes_since_change,
            is_window_open,
        )

        if hvac_mode in ("off", "dry", "fan_only"):
            return self._payload(
                status="Idle",
                fan_mode=current_fan,
                reason=f"HVAC mode '{hvac_mode}' is not simulated",
                would_change_now="no",
            )

        fan_modes = self._fan_modes or ([] if current_fan is None else [current_fan])
        if not fan_modes:
            return self._payload(
                status="Unavailable",
                fan_mode=current_fan,
                reason="No fan modes available yet",
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
            is_hvac_idle=is_hvac_idle,
        )

        if is_window_open:
            return self._payload(
                status="Disturbed",
                fan_mode=active_fan,
                reason="Window open detected: MPC paused",
                would_change_now="no",
                dead_time=dead_time,
                disturbance_bias=self._disturbance_bias,
            )

        if is_defrost_active:
            return self._payload(
                status="Disturbed",
                fan_mode=active_fan,
                reason="Defrost active: MPC paused",
                would_change_now="no",
                dead_time=dead_time,
                disturbance_bias=self._disturbance_bias,
            )

        if is_hvac_idle:
            return self._payload(
                status="Disturbed",
                fan_mode=active_fan,
                reason="HVAC idle: compressor off, MPC paused",
                would_change_now="no",
                dead_time=dead_time,
                disturbance_bias=self._disturbance_bias,
            )

        # Setpoint drop: when the target moves far away (e.g. night setpoint),
        # there is no point running the full MPC cost optimisation — the answer
        # is always the lowest mode.
        current_error = self._temperature_error(current_temp, target_temp, hvac_mode)
        if current_error < THRESHOLD_TARGET_DROP:
            lowest_fan = fan_modes[0]
            would_change = "yes" if active_fan != lowest_fan else "no"
            return self._payload(
                status="Setpoint drop",
                fan_mode=lowest_fan,
                reason=f"Setpoint drop: target moved away ({current_error:.1f}°C), minimum speed",
                would_change_now=would_change,
                dead_time=dead_time,
                disturbance_bias=self._disturbance_bias,
            )

        simulations: list[ModeSimulation] = []
        known_profiles = 0
        worst_spread = 0.0
        current_index = fan_modes.index(active_fan)

        # Build monotone-enforced slope map so higher fan modes are never
        # assigned a lower slope than lower modes.  Only applied when ALL
        # profiles are learned; on a fresh install (partial profiles) the
        # raw learned / fallback values are used as-is.
        monotone_slopes = self.build_monotone_slopes(fan_modes, hvac_mode)

        # Ensure the horizon is at least dead_time + base_horizon so that
        # any mode changes are simulated for at least a full default horizon (e.g. 30 minutes)
        # of their actual candidate slope, preventing the "dead-time blindness".
        sim_horizon = max(self._horizon_minutes, int(dead_time) + self._horizon_minutes)

        for fan_mode in fan_modes:
            mode_slope, known_profile = self._get_mode_slope(
                fan_mode,
                hvac_mode,
                active_fan,
                current_effective_slope,
                fan_modes,
                monotone_slopes,
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
                horizon_minutes=sim_horizon,
            )
            simulations.append(sim)
            known_profiles += int(known_profile)
            if known_profile:
                spread = self._learning.get_profile_spread(fan_mode, hvac_mode)
                if spread is not None:
                    worst_spread = max(worst_spread, spread)

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

        confidence = self._compute_confidence(known_profiles, len(fan_modes), phase, worst_spread)
        would_change_now = "yes" if change_allowed and best.fan_mode != active_fan else "no"
        status = "Ready" if confidence >= 0.5 else "Low confidence"
        _LOGGER.debug(
            "MPC candidates: %s",
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
            f"MPC recommends {best.fan_mode}: cost={best.total_cost:.2f}, "
            f"T+10={best.predicted_temp_10m:.2f}C, T+30={best.predicted_temp_30m:.2f}C"
        )
        if selection_note:
            reason += f" | {selection_note}"
        # Surface capacity saturation: strongest fan selected yet still well short
        # of target means the HVAC system is capacity-bound, not a control issue.
        if best.fan_mode == fan_modes[-1] and current_error > self._deadband:
            reason += f" | Saturated: max fan, {current_error:.1f}C from target (capacity-bound)"
        if abs(self._disturbance_bias) >= 0.05:
            reason += f" | Bias={self._disturbance_bias:+.2f}C/h"
        if not change_allowed:
            reason += (
                f" | Min interval active ({minutes_since_change:.1f}/"
                f"{self._min_interval:.1f} min)"
            )

        _LOGGER.debug(
            "MPC result: active=%s best=%s status=%s confidence=%.2f known_profiles=%d/%d bias=%.3f note=%s",
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
        monotone_slopes: dict[str, float] | None = None,
    ) -> tuple[float, bool]:
        """Return the effective slope estimate for a candidate fan mode."""
        if monotone_slopes is not None and fan_mode in monotone_slopes:
            learned = monotone_slopes[fan_mode]
        else:
            learned = self._learning.get_mode_effective_slope(fan_mode, hvac_mode)
        if learned is not None:
            _LOGGER.debug(
                "MPC slope model: using learned profile for %s/%s = %.3f",
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
            "MPC slope model: using fallback for %s/%s = %.3f (baseline=%.3f current_fan=%s)",
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

    def build_monotone_slopes(self, fan_modes: list[str], hvac_mode: str) -> dict[str, float]:
        """Return monotone-enforced slopes for all known profiles.

        Fan modes are assumed ordered from weakest to strongest.  For each mode
        with a learned profile, the slope is clamped to be >= the slope of the
        last known lower mode (isotonic forward pass).  Modes without a learned
        profile are omitted from the result — the caller falls back to rank-scaled
        estimation for those.

        Previously returned None when any profile was missing.  Now always
        returns a (possibly empty) dict so known adjacent profiles are always
        monotone-enforced even during partial learning.
        """
        enforced: dict[str, float] = {}
        prev = float("-inf")
        changed = False
        for fm in fan_modes:
            slope = self._learning.get_mode_effective_slope(fm, hvac_mode)
            if slope is None:
                continue  # unknown profile — skip, caller uses rank-scaling fallback
            clamped = max(slope, prev)
            if clamped != slope:
                changed = True
            enforced[fm] = clamped
            prev = clamped

        if changed:
            _LOGGER.debug(
                "Monotone enforcement applied for %s: %s",
                hvac_mode,
                {fm: round(v, 3) for fm, v in enforced.items()},
            )
        return enforced

    def _update_disturbance_bias(
        self,
        *,
        observed_effective_slope: float,
        expected_effective_slope: float,
        known_profile: bool,
        phase: str,
        is_window_open: bool,
        is_defrost_active: bool = False,
        is_hvac_idle: bool = False,
    ) -> None:
        """Track slow external disturbances such as solar gains or occupancy."""
        if is_window_open or is_defrost_active or is_hvac_idle:
            self._disturbance_bias *= DISTURBANCE_DECAY
            if is_window_open:
                decay_reason = "window is open"
            elif is_defrost_active:
                decay_reason = "defrost is active"
            else:
                decay_reason = "HVAC compressor is idle"
            _LOGGER.debug(
                "MPC disturbance bias decayed to %.3f because %s",
                self._disturbance_bias,
                decay_reason,
            )
            return

        if not known_profile or phase != PHASE_ESTABLISHED:
            self._disturbance_bias *= DISTURBANCE_DECAY
            _LOGGER.debug(
                "MPC disturbance bias decayed to %.3f because known_profile=%s phase=%s",
                self._disturbance_bias,
                known_profile,
                phase,
            )
            return

        residual = observed_effective_slope - expected_effective_slope
        updated = ((1 - DISTURBANCE_EMA_ALPHA) * self._disturbance_bias) + (DISTURBANCE_EMA_ALPHA * residual)
        self._disturbance_bias = max(-MAX_DISTURBANCE_BIAS, min(MAX_DISTURBANCE_BIAS, updated))
        _LOGGER.debug(
            "MPC disturbance bias updated to %.3f (observed=%.3f expected=%.3f residual=%.3f)",
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
        horizon_minutes: int | None = None,
    ) -> ModeSimulation:
        """Simulate one constant fan mode over the prediction horizon."""
        horizon = horizon_minutes if horizon_minutes is not None else self._horizon_minutes
        steps = max(1, int(horizon / self._cycle_minutes))
        step_hours = self._cycle_minutes / 60.0
        blend = 0.45
        sim_temp = current_temp
        predicted_10m = None
        predicted_30m = None
        cost = 0.0
        thermal_power = current_effective_slope
        change_delay = 0.0 if candidate_fan == current_fan else dead_time
        candidate_effective_slope = candidate_mode_slope + self._disturbance_bias

        for step in range(1, steps + 1):
            elapsed = step * self._cycle_minutes
            if elapsed <= change_delay:
                target_effective_slope = current_effective_slope
            else:
                target_effective_slope = candidate_effective_slope

            thermal_power += blend * (target_effective_slope - thermal_power)
            raw_slope = -thermal_power if hvac_mode == "cool" else thermal_power
            sim_temp += step_hours * raw_slope

            if elapsed >= 10 and predicted_10m is None:
                predicted_10m = sim_temp
            if elapsed >= 30 and predicted_30m is None:
                predicted_30m = sim_temp

            error = self._temperature_error(sim_temp, target_temp, hvac_mode)
            comfort_error = max(abs(error) - self._deadband, 0.0)
            overshoot = max(-error, 0.0)
            floor_violation = max(target_temp - sim_temp, 0.0) if hvac_mode == "heat" else max(sim_temp - target_temp, 0.0)

            # Step-by-step urgency weight calculated dynamically based on current simulated step comfort error
            step_urgency_weight = 1.0 + comfort_error * URGENCY_SENSITIVITY
            cost += COMFORT_ERROR_WEIGHT * comfort_error * step_urgency_weight
            cost += OVERSHOOT_QUADRATIC_WEIGHT * overshoot * overshoot
            cost += FLOOR_VIOLATION_LINEAR_WEIGHT * floor_violation * step_urgency_weight
            cost += FLOOR_VIOLATION_QUADRATIC_WEIGHT * floor_violation * floor_violation

        cost += MODE_CHANGE_DISTANCE_COST * abs(candidate_index - current_index)
        # Apply a non-linear economic mode-ranking cost representing physical power
        # scaling. Relative power grows geometrically with the mode rank so every
        # mode is differentiated regardless of how many the climate entity exposes
        # (a 4-mode system reproduces the previous 1.0 / 1.8 / 3.3 / 6.0 ramp).
        relative_power = MODE_POWER_RATIO ** candidate_index
        cost += MODE_RANK_COST * relative_power
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

    def _compute_confidence(self, known_profiles: int, total_profiles: int, phase: str, worst_spread: float = 0.0) -> float:
        """Return a coarse confidence score for the current recommendation.

        Confidence is driven primarily by per-mode profile *coverage* and
        *quality* (spread), not by the global readiness flag.  Previously the
        global ``is_ready()`` threshold (240 samples) halved the score, so a
        controller with every per-mode profile fully learned could still be
        stuck reporting "Low confidence" until that global count was reached —
        which never happened for an HVAC mode used only part of the year.

        - coverage  : fraction of fan modes with a learned profile (main driver).
        - readiness : small bonus once the global sample threshold is reached.
        - phase     : transient/dead-time phases attenuate confidence.
        - penalties : sustained disturbance bias and high profile spread.

        worst_spread is the maximum MAD/median ratio across all known profiles.
        Profiles with high spread (> 0.15) reduce confidence proportionally,
        capped at a 0.20 penalty.
        """
        coverage = known_profiles / max(total_profiles, 1)
        coverage_score = 0.3 + 0.7 * coverage
        readiness_bonus = 0.1 if self._learning.is_ready() else 0.0
        phase_factor = 1.0 if phase == PHASE_ESTABLISHED else 0.85
        disturbance_penalty = min(abs(self._disturbance_bias) / MAX_DISTURBANCE_BIAS, 0.35)
        spread_penalty = min(max(worst_spread - 0.15, 0.0) * 0.4, 0.20)
        return max(0.1, min(1.0, (coverage_score + readiness_bonus) * phase_factor - disturbance_penalty - spread_penalty))

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
        would_change_now: str = "no",
        dead_time: float | None = None,
        known_profiles: int = 0,
        disturbance_bias: float | None = None,
    ) -> dict:
        """Build the MPC payload injected into sensors and CSV logs."""
        return {
            "mpc_status": status,
            "mpc_fan_mode": fan_mode,
            "mpc_reason": reason,
            "mpc_predicted_temperature_10m": round(predicted_10m, 2) if predicted_10m is not None else None,
            "mpc_predicted_temperature_30m": round(predicted_30m, 2) if predicted_30m is not None else None,
            "mpc_cost": round(cost, 3) if cost is not None else None,
            "mpc_confidence": round(confidence, 1) if confidence is not None else None,
            "mpc_would_change_now": would_change_now,
            "mpc_dead_time": round(dead_time, 2) if dead_time is not None else None,
            "mpc_known_profiles": known_profiles,
            "mpc_disturbance_bias": round(disturbance_bias, 3) if disturbance_bias is not None else None,
        }

    @staticmethod
    def _temperature_error(temp: float, target_temp: float, hvac_mode: str) -> float:
        """Return the signed comfort error aligned with the active HVAC mode."""
        return (temp - target_temp) if hvac_mode == "cool" else (target_temp - temp)
