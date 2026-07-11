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
    REFERENCE_SLOPE_ERROR,
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

# The configured min_interval is a floor; once learning is ready the effective
# dwell before a fan change is raised toward the learned dead time (you cannot
# observe a change's effect faster than the dead time, so changing sooner just
# invites oscillation). The rise is capped at this factor x the configured floor
# so a spuriously large learned dead time cannot stall the controller.
MAX_ADAPTIVE_INTERVAL_FACTOR = 3.0

# The dead-time lock above has no visibility into whether the current fan mode
# is actually holding comfort — a misjudged step (e.g. a multi-rank drop) can
# leave the room drifting away from target for the full adaptive interval with
# no escape. A static error threshold does not fit every deadband/system, so
# instead this tracks how much the comfort error has *grown* since the fan
# last changed (comparing against the error at that moment) — a direct read of
# the room's actual trajectory rather than an arbitrary absolute cutoff. Past
# this much growth, an *escalation only* (never a step-down) is allowed to
# bypass the lock.
DEAD_TIME_ESCALATION_GROWTH = 0.15  # degC comfort error allowed to worsen since the change

# --- Hold-equilibrium (economic) mode -------------------------------------
# When enabled, near the setpoint the controller matches fan output to the
# system's steady thermal production instead of collapsing to the lowest speed.
# Rationale: on a running heat pump the compressor draws the dominant power and
# keeps producing cold/heat regardless of fan speed, so the fan-rank penalty
# (which models fan watts) optimises the wrong term there. Holding the room flat
# with a steady speed avoids the drift-then-blast cycle and lets an inverter
# compressor modulate at high COP instead of short-cycling. A small, bounded cold
# undershoot is tolerated so the holding speed can slightly lead the load rather
# than lag it — this is what lets a discrete speed ladder actually hold.
# Enabled by default: on the 899 h production trace it shifted ~14% of time from
# `low` to a steady `med` hold and cut fan changes 357->313 with no change in
# predicted comfort (MAE T+10) or average cost. The feature is dormant whenever
# the error exceeds the deadband, so far-from-setpoint escalation is untouched.
HOLD_EQUILIBRIUM = True
HOLD_UNDERSHOOT_TOLERANCE = 0.3  # °C of free wrong-side undershoot inside the hold zone
HOLD_RANK_SCALE = 0.15  # shrink the fan-rank (energy) penalty to a tie-breaker in the zone

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

# Guard against jumping straight to a fan mode more than one rank below the
# current one when that candidate's own learned profile shows it cannot
# sustain progress (net effective slope <= 0 at the reference error). The
# cost-based forecast cannot be trusted for this: during the dead-time-blind
# window (see sim_horizon below) every candidate's near-term trajectory is
# dominated by the *current* mode's momentum, not the candidate's own
# behaviour, so a weak mode can look deceptively good right up until the
# switch is committed. Adjacent-rank switches are left untouched — this only
# blocks multi-rank plunges to a mode with no track record of holding.
MIN_VIABLE_MULTI_RANK_STEPDOWN_SLOPE = 0.0

# Grey-box feasibility gate (used only when an outdoor sensor is configured and
# the envelope model is learned). A step-down candidate is excluded when its
# predicted effective slope toward target *at the setpoint* falls below this
# margin — i.e. it cannot hold the room against the current outdoor load. The
# small negative tolerance lets a borderline mode (predicted ≈0) through so only
# clear failures (a mode that actively loses ground) are blocked.
FEASIBILITY_HOLD_MARGIN = -0.05

# When enabled and an outdoor sensor + learned envelope are available, the MPC
# projects each candidate's temperature with the grey-box model
# ``dT/dt = k_env·(T_ext − T) + u_fan`` instead of the comfort-error gap model.
# This uses each fan's ambient-decoupled own power (u_fan) — the clean estimate
# that keeps learning near the setpoint — so the projection no longer relies on
# the gap slopes that the stagnation filter starves for the weak modes. The
# solar residual not captured by k_env·(T_ext−T) is left to the feedback loop
# (it is small relative to the fan/compressor term). Dormant without an outdoor
# sensor; toggled off here reverts to the gap-model projection for A/B.
#
# OFF by default: an open-loop replay A/B on the 899 h trace showed it slightly
# WORSE than the gap-model projection (fan changes 315→352, cost 652→673, comfort
# MAE flat) — near/below the setpoint the envelope correctly sees the weak modes
# warming the room back toward target and switches to them more often, which
# open-loop replay penalises as extra churn. Kept as a gated, tested path for a
# proper closed-loop A/B on-device; not enabled on replay evidence alone.
USE_ENVELOPE_PROJECTION = False


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
        fan_modes: list[str] | None = None,
        horizon_minutes: int = 30,
        cycle_minutes: int = DELTA_TIME_CONTROL_LOOP,
    ) -> None:
        self._learning = learning
        self._deadband = deadband
        self._min_interval = min_interval
        self._fan_modes = fan_modes
        self._horizon_minutes = horizon_minutes
        self._cycle_minutes = cycle_minutes
        self._disturbance_bias = 0.0
        self._error_at_lock_start: float | None = None

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

    def get_effective_timeout(self, hvac_mode: str = "unknown") -> float:
        """Return the adaptive advisory timeout (diagnostic only).

        This value is surfaced in sensors and the data-collection CSV to show how
        long the controller would wait before forcing a re-evaluation; it does not
        gate any control decision (that is the job of ``min_interval``). Before
        learning is ready it falls back to the default dead time scaled by the
        safety factor.
        """
        if self._learning.is_ready():
            learned_dead_time = self._learning.get_dead_time(hvac_mode)
            return max(self._min_interval, learned_dead_time * DEAD_TIME_SAFETY_FACTOR)
        return DEFAULT_DEAD_TIME * DEAD_TIME_SAFETY_FACTOR

    def _effective_min_interval(self, dead_time: float) -> float:
        """Return the minimum dwell (minutes) before a fan change is allowed.

        The configured ``min_interval`` is a floor. Once learning is ready the
        effective dwell is raised toward the learned ``dead_time`` — changing the
        fan faster than the dead time means acting before the previous change's
        effect can be observed, a guaranteed source of oscillation. The rise is
        capped at ``MAX_ADAPTIVE_INTERVAL_FACTOR`` × the configured floor so a
        spuriously large learned dead time cannot make the controller sluggish.
        Urgent overrides (setpoint drop, window/defrost/idle) are handled before
        this gate, so they are never blocked by a long adaptive interval.
        """
        if not self._learning.is_ready():
            return float(self._min_interval)
        capped = min(dead_time, self._min_interval * MAX_ADAPTIVE_INTERVAL_FACTOR)
        return max(float(self._min_interval), capped)

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
        outdoor_temp: float | None = None,
    ) -> dict:
        """Evaluate the best fan mode for the current cycle.

        ``outdoor_temp`` is optional: when provided and the grey-box envelope
        model is learned for this hvac mode, a per-fan feasibility gate excludes
        speeds that cannot hold the setpoint at the current outdoor temperature
        (see docs/effective_slope_analysis.md). When absent, behaviour is
        unchanged and the raw-slope multi-rank guard is used instead.
        """
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
        current_error = self._temperature_error(current_temp, target_temp, hvac_mode)
        dead_time = self._learning.get_dead_time()
        effective_min_interval = self._effective_min_interval(dead_time)
        change_allowed = minutes_since_change >= effective_min_interval
        # Snapshot the comfort error at the start of each hold so growth since
        # the fan last changed can be measured (see DEAD_TIME_ESCALATION_GROWTH).
        if minutes_since_change < self._cycle_minutes or self._error_at_lock_start is None:
            self._error_at_lock_start = current_error
        error_growth_since_change = current_error - self._error_at_lock_start
        phase = self._detect_phase(minutes_since_change, dead_time)
        current_mode_slope, current_known_profile = self._get_mode_slope(
            active_fan,
            hvac_mode,
            active_fan,
            current_effective_slope,
            fan_modes,
        )
        # Compare the observed slope against what the gap-dependent model expects
        # *at the current error*, not at the reference gap. This keeps the
        # disturbance bias clean: it only captures genuine external disturbances
        # (solar gain, occupancy) instead of the systematic variation of cooling
        # power with the distance to setpoint.
        current_mode_gain = (
            self._learning.get_mode_slope_gain(active_fan, hvac_mode) if current_known_profile else 0.0
        )
        expected_slope_now = self._gap_slope(current_mode_slope, current_mode_gain, current_error)
        self._update_disturbance_bias(
            observed_effective_slope=current_effective_slope,
            expected_effective_slope=expected_slope_now,
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
            mode_gain = self._learning.get_mode_slope_gain(fan_mode, hvac_mode) if known_profile else 0.0
            envelope_params = self._envelope_params(fan_mode, hvac_mode, outdoor_temp)
            sim = self._simulate_mode(
                current_temp=current_temp,
                target_temp=target_temp,
                hvac_mode=hvac_mode,
                current_fan=active_fan,
                candidate_fan=fan_mode,
                current_effective_slope=current_effective_slope,
                candidate_mode_slope=mode_slope,
                candidate_mode_gain=mode_gain,
                dead_time=dead_time,
                candidate_index=fan_modes.index(fan_mode),
                current_index=current_index,
                change_allowed=change_allowed,
                known_profile=known_profile,
                horizon_minutes=sim_horizon,
                outdoor_temp=outdoor_temp,
                envelope_params=envelope_params,
            )
            simulations.append(sim)
            known_profiles += int(known_profile)
            if known_profile:
                spread = self._learning.get_profile_spread(fan_mode, hvac_mode)
                if spread is not None:
                    worst_spread = max(worst_spread, spread)

        current_simulation = next(sim for sim in simulations if sim.fan_mode == active_fan)
        unfiltered_best = min(simulations, key=lambda item: item.total_cost)

        def _stepdown_capable(sim: ModeSimulation) -> bool:
            candidate_index = fan_modes.index(sim.fan_mode)
            if candidate_index >= current_index:
                return True  # upward / same rank is always allowed
            # Envelope feasibility (preferred): can this fan hold the setpoint at
            # the current outdoor temperature? Applies to a step-down of any size.
            if outdoor_temp is not None:
                predicted = self._learning.envelope_predicted_slope(
                    sim.fan_mode, hvac_mode, outdoor_temp - target_temp
                )
                if predicted is not None:
                    effective = -predicted if hvac_mode == "cool" else predicted
                    return effective > FEASIBILITY_HOLD_MARGIN
            # Fallback (no outdoor sensor / envelope not learned): the original
            # raw-slope guard, restricted to multi-rank plunges.
            if current_index - candidate_index <= 1:
                return True
            raw_slope = self._learning.get_mode_effective_slope(sim.fan_mode, hvac_mode)
            return raw_slope is None or raw_slope > MIN_VIABLE_MULTI_RANK_STEPDOWN_SLOPE

        eligible_simulations = [sim for sim in simulations if _stepdown_capable(sim)]
        best = min(eligible_simulations, key=lambda item: item.total_cost)
        selection_note = ""
        blocked_note = ""

        if unfiltered_best.fan_mode != best.fan_mode:
            blocked_index = fan_modes.index(unfiltered_best.fan_mode)
            ranks = current_index - blocked_index
            if outdoor_temp is not None and self._learning.envelope_predicted_slope(
                unfiltered_best.fan_mode, hvac_mode, outdoor_temp - target_temp
            ) is not None:
                predicted = self._learning.envelope_predicted_slope(
                    unfiltered_best.fan_mode, hvac_mode, outdoor_temp - target_temp
                )
                blocked_note = (
                    f"Blocked drop to {unfiltered_best.fan_mode}: can't hold the setpoint at "
                    f"{outdoor_temp:.1f}C outdoor (predicted {predicted:+.2f}C/h)"
                )
            else:
                blocked_slope = self._learning.get_mode_effective_slope(unfiltered_best.fan_mode, hvac_mode)
                blocked_note = (
                    f"Blocked {ranks}-rank drop to {unfiltered_best.fan_mode}: "
                    f"its own profile ({blocked_slope:.2f}C/h) can't sustain progress"
                )

        if not change_allowed and best.fan_mode != active_fan:
            best_index = fan_modes.index(best.fan_mode)
            if best_index > current_index and error_growth_since_change > DEAD_TIME_ESCALATION_GROWTH:
                selection_note = (
                    f"Emergency escalation to {best.fan_mode}: comfort error worsened by "
                    f"{error_growth_since_change:.2f}C since the change bypasses the min interval"
                )
                change_allowed = True
            else:
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
        if blocked_note:
            reason += f" | {blocked_note}"
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
                f"{effective_min_interval:.1f} min)"
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
    def _gap_slope(reference_slope: float, gain: float, error: float) -> float:
        """Return the modelled effective slope at a given comfort error.

        ``reference_slope`` is the representative slope at REFERENCE_SLOPE_ERROR
        (a + b·REF) and ``gain`` is b, so the model at ``error`` is
        reference_slope + b·(error − REF). The error is floored at 0 (no driving
        force at/below setpoint) and the result is floored at 0 so the model never
        projects active cooling/heating away from the setpoint; the additive
        disturbance bias is applied separately by the caller.
        """
        modelled = reference_slope + gain * (max(error, 0.0) - REFERENCE_SLOPE_ERROR)
        return max(0.0, modelled)

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

    def _envelope_params(
        self, fan_mode: str, hvac_mode: str, outdoor_temp: float | None
    ) -> tuple[float, float] | None:
        """Return ``(k_env, u_fan)`` for envelope projection, or None when unavailable.

        Requires the feature enabled, an outdoor temperature, and a learned
        envelope (conductance + this fan's own power). Otherwise the caller falls
        back to the gap-model projection.
        """
        if not USE_ENVELOPE_PROJECTION or outdoor_temp is None:
            return None
        k_env = self._learning.get_envelope_conductance(hvac_mode)
        u_fan = self._learning.get_mode_cooling_power(fan_mode, hvac_mode)
        if k_env is None or u_fan is None:
            return None
        return (k_env, u_fan)

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
        candidate_mode_gain: float = 0.0,
        dead_time: float,
        candidate_index: int,
        current_index: int,
        change_allowed: bool,
        known_profile: bool,
        horizon_minutes: int | None = None,
        outdoor_temp: float | None = None,
        envelope_params: tuple[float, float] | None = None,
    ) -> ModeSimulation:
        """Simulate one fan mode over the prediction horizon.

        The candidate's effective slope is gap-dependent: at each step it is
        recomputed from the simulated comfort error as
        ``candidate_mode_slope + candidate_mode_gain·(error − REFERENCE_SLOPE_ERROR)``
        (floored at 0), plus the disturbance bias. This makes the projection
        decelerate realistically as the room approaches the setpoint instead of
        cooling/heating at a constant rate, eliminating the phantom overshoot that a
        constant-slope model produces past the target.
        """
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
        # Hold zone: within one deadband of the setpoint we optimise for holding
        # equilibrium (match the compressor's steady output) rather than for the
        # lowest fan rank. See the HOLD_EQUILIBRIUM constant block for rationale.
        hold_active = (
            HOLD_EQUILIBRIUM
            and abs(self._temperature_error(current_temp, target_temp, hvac_mode)) <= self._deadband
        )
        undershoot_tolerance = HOLD_UNDERSHOOT_TOLERANCE if hold_active else 0.0

        for step in range(1, steps + 1):
            elapsed = step * self._cycle_minutes
            if elapsed <= change_delay:
                target_effective_slope = current_effective_slope
            elif envelope_params is not None and outdoor_temp is not None:
                # Grey-box projection: dT/dt = k_env·(T_ext − T) + u_fan, expressed
                # as an effective slope toward target (positive = making progress).
                k_env, u_fan = envelope_params
                env_dtdt = k_env * (outdoor_temp - sim_temp) + u_fan
                target_effective_slope = -env_dtdt if hvac_mode == "cool" else env_dtdt
            else:
                step_error = self._temperature_error(sim_temp, target_temp, hvac_mode)
                target_effective_slope = (
                    self._gap_slope(candidate_mode_slope, candidate_mode_gain, step_error)
                    + self._disturbance_bias
                )

            thermal_power += blend * (target_effective_slope - thermal_power)
            raw_slope = -thermal_power if hvac_mode == "cool" else thermal_power
            sim_temp += step_hours * raw_slope

            if elapsed >= 10 and predicted_10m is None:
                predicted_10m = sim_temp
            if elapsed >= 30 and predicted_30m is None:
                predicted_30m = sim_temp

            error = self._temperature_error(sim_temp, target_temp, hvac_mode)
            comfort_error = max(abs(error) - self._deadband, 0.0)
            overshoot = max(-error - undershoot_tolerance, 0.0)
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
        rank_scale = HOLD_RANK_SCALE if hold_active else 1.0
        cost += MODE_RANK_COST * relative_power * rank_scale
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
