#!/usr/bin/env python3
"""Offline replay bench for Smart Fan Controller MPC.

Replays data-collection CSV traces through the MPC controller with configurable
cost weights, producing comparison metrics across variants.

NOTE: This is an open-loop replay — real temperature data from the CSV is used
as-is, but the *simulated fan state* is tracked per variant.  Because the
thermal trajectory is fixed, results show "what would MPC have decided" rather
than "what would have happened".  This is valid for comparing cost-weight
sensitivity on the same trace.

Usage:
    python scripts/replay_bench.py data.csv
    python scripts/replay_bench.py data.csv --variant baseline --variant high_rank:MODE_RANK_COST=0.3
    python scripts/replay_bench.py data.csv \\
        --variant baseline \\
        --variant aggressive:COMFORT_ERROR_WEIGHT=2.0,OVERSHOOT_QUADRATIC_WEIGHT=5.0 \\
        --output replay_results.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Make the custom component importable
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "custom_components"))

from smart_fan_controller import mpc_controller as _mpc_mod  # noqa: E402
from smart_fan_controller.mpc_controller import MPCController  # noqa: E402
from smart_fan_controller.thermal_learning import ThermalLearning  # noqa: E402

# Quiet the debug chatter during bulk replay
logging.getLogger("custom_components.smart_fan_controller").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Tunable cost constants (module-level in mpc_controller.py)
# ---------------------------------------------------------------------------
TUNABLE = [
    "COMFORT_ERROR_WEIGHT",
    "OVERSHOOT_QUADRATIC_WEIGHT",
    "FLOOR_VIOLATION_LINEAR_WEIGHT",
    "FLOOR_VIOLATION_QUADRATIC_WEIGHT",
    "MODE_CHANGE_DISTANCE_COST",
    "MODE_RANK_COST",
    "MIN_INTERVAL_CHANGE_PENALTY",
    "URGENCY_SENSITIVITY",
]
_DEFAULTS = {k: getattr(_mpc_mod, k) for k in TUNABLE}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Row:
    """One parsed CSV row."""

    timestamp: str
    hvac_mode: str
    current_temp: float
    target_temp: float
    vtherm_slope: float
    current_fan: str
    decided_fan: str
    minutes_since_change: float
    is_window_open: bool
    defrost_active: bool
    hvac_idle: bool
    mpc_fan: str
    mpc_status: str
    phase: str
    dead_time: float


@dataclass
class Metrics:
    """Aggregate metrics for one replay variant."""

    name: str
    overrides: dict[str, float]
    total_rows: int = 0
    active_rows: int = 0
    fan_changes: int = 0
    fan_dist: Counter = field(default_factory=Counter)
    costs: list[float] = field(default_factory=list)
    agree_with_live: int = 0
    prediction_errors_10m: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------
def load_csv(path: str) -> list[Row]:
    """Parse a data_collection CSV into Row objects."""
    rows: list[Row] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append(
                    Row(
                        timestamp=r["timestamp"],
                        hvac_mode=r["hvac_mode"],
                        current_temp=float(r["current_temp"]),
                        target_temp=float(r["target_temp"]),
                        vtherm_slope=float(r["vtherm_slope"]),
                        current_fan=r["current_fan"],
                        decided_fan=r["decided_fan"],
                        minutes_since_change=float(r["minutes_since_change"]),
                        is_window_open=bool(int(r.get("is_window_open", "0"))),
                        defrost_active=bool(int(r.get("defrost_active", "0"))),
                        hvac_idle=bool(int(r.get("hvac_idle", "0"))),
                        mpc_fan=r.get("mpc_fan", ""),
                        mpc_status=r.get("mpc_status", ""),
                        phase=r.get("phase", ""),
                        dead_time=float(r["dead_time"]) if r.get("dead_time") else 10.0,
                    )
                )
            except (ValueError, KeyError) as exc:
                print(f"  Warning: skipping row: {exc}", file=sys.stderr)
    return rows


# ---------------------------------------------------------------------------
# Learning profile builder
# ---------------------------------------------------------------------------
def build_learning(rows: list[Row]) -> ThermalLearning:
    """Seed a ThermalLearning from established, undisturbed CSV rows."""
    learning = ThermalLearning()
    for row in rows:
        if row.phase != "ESTABLISHED":
            continue
        if row.is_window_open or row.defrost_active or row.hvac_idle:
            continue
        if row.hvac_mode in ("off", "dry", "fan_only"):
            continue
        if not row.current_fan:
            continue
        error = (
            row.current_temp - row.target_temp
            if row.hvac_mode == "cool"
            else row.target_temp - row.current_temp
        )
        learning.add_slope_sample(
            fan_mode=row.current_fan,
            slope=row.vtherm_slope,
            temperature_error=error,
            hvac_mode=row.hvac_mode,
        )
    return learning


def detect_fan_modes(rows: list[Row]) -> list[str]:
    """Return fan modes in first-seen order."""
    seen: dict[str, None] = {}
    for row in rows:
        if row.current_fan and row.current_fan not in seen:
            seen[row.current_fan] = None
    return list(seen.keys())


# ---------------------------------------------------------------------------
# Variant replay (semi-closed-loop: simulated fan, real temperatures)
# ---------------------------------------------------------------------------
def _apply(overrides: dict[str, float]) -> None:
    """Monkey-patch mpc_controller cost constants."""
    for k, v in overrides.items():
        setattr(_mpc_mod, k, v)


def _restore() -> None:
    """Restore original mpc_controller cost constants."""
    for k, v in _DEFAULTS.items():
        setattr(_mpc_mod, k, v)


def replay(
    rows: list[Row],
    learning: ThermalLearning,
    fan_modes: list[str],
    overrides: dict[str, float],
    deadband: float,
    min_interval: int,
    cycle_minutes: int = 2,
) -> list[tuple[dict, str]]:
    """Replay CSV through MPC, tracking simulated fan state.

    Returns [(mpc_payload, simulated_fan), ...] for each row.
    """
    _apply(overrides)
    try:
        mpc = MPCController(
            learning=learning,
            deadband=deadband,
            min_interval=min_interval,
            fan_modes=fan_modes,
        )
        results: list[tuple[dict, str]] = []
        sim_fan = rows[0].current_fan if rows else fan_modes[0]
        sim_minutes = rows[0].minutes_since_change if rows else 0.0

        for row in rows:
            payload = mpc.evaluate(
                current_temp=row.current_temp,
                target_temp=row.target_temp,
                vtherm_slope=row.vtherm_slope,
                hvac_mode=row.hvac_mode,
                current_fan=sim_fan,
                is_window_open=row.is_window_open,
                is_defrost_active=row.defrost_active,
                is_hvac_idle=row.hvac_idle,
                minutes_since_change=sim_minutes,
            )
            rec_fan = payload.get("mpc_fan_mode") or sim_fan
            would_change = payload.get("mpc_would_change_now", "no")

            if would_change == "yes" and rec_fan != sim_fan:
                sim_fan = rec_fan
                sim_minutes = 0.0
            else:
                sim_minutes += cycle_minutes

            results.append((payload, sim_fan))
        return results
    finally:
        _restore()


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------
def compute_metrics(
    name: str,
    overrides: dict[str, float],
    rows: list[Row],
    results: list[tuple[dict, str]],
    deadband: float,
    cycle_minutes: int = 2,
) -> Metrics:
    """Compute aggregate metrics from a replay run."""
    m = Metrics(name=name, overrides=overrides, total_rows=len(rows))
    prev_fan: str | None = None

    for i, (row, (payload, sim_fan)) in enumerate(zip(rows, results)):
        status = payload.get("mpc_status", "")

        m.fan_dist[sim_fan] += 1

        if status in ("Disturbed", "Idle", "Unavailable"):
            prev_fan = sim_fan
            continue

        m.active_rows += 1

        # Fan change
        if prev_fan is not None and sim_fan != prev_fan:
            m.fan_changes += 1

        # Agreement with live system
        if sim_fan == row.decided_fan:
            m.agree_with_live += 1

        # Cost
        cost = payload.get("mpc_cost")
        if cost is not None:
            m.costs.append(cost)

        # Prediction error at T+10 (lookahead rows)
        pred_10 = payload.get("mpc_predicted_temperature_10m")
        lookahead = 10 // cycle_minutes
        if pred_10 is not None and (i + lookahead) < len(rows):
            actual = rows[i + lookahead].current_temp
            m.prediction_errors_10m.append(abs(pred_10 - actual))

        prev_fan = sim_fan

    return m


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_report(
    rows: list[Row],
    fan_modes: list[str],
    learning: ThermalLearning,
    metrics_list: list[Metrics],
    deadband: float,
    cycle_minutes: int = 2,
) -> None:
    """Print a formatted comparison report."""
    total_hours = len(rows) * cycle_minutes / 60.0

    # --- Learning profiles ---
    print("\n=== Learning Profiles ===")
    hvac_modes_seen = sorted(
        {r.hvac_mode for r in rows if r.hvac_mode not in ("off", "dry", "fan_only")}
    )
    for hvac in hvac_modes_seen:
        profiles = learning.get_mode_profiles(hvac, fan_modes)
        print(f"\n  {hvac}:")
        for mode, info in profiles.items():
            slope = info["effective_slope"]
            samples = info["samples"]
            status = "+" if info["ready"] else "."
            slope_str = f"{slope:+.3f} C/h" if slope is not None else "n/a"
            print(f"    {status} {mode:>10s}: {slope_str}  ({samples} samples)")

    print(f"\n  Dead time: {learning.get_dead_time():.1f} min")
    print(f"  Total slope samples: {learning.slope_sample_count()}")
    print(f"  Trace duration: {total_hours:.1f} hours ({len(rows)} rows)")

    # --- Variant comparison table ---
    print("\n=== Variant Comparison ===\n")
    col_w = max(18, max(len(m.name) for m in metrics_list) + 4)
    header = f"{'Metric':<32s}"
    for m in metrics_list:
        header += f"{m.name:>{col_w}s}"
    print(header)
    print("-" * len(header))

    def _row(label: str, values: list[str]) -> None:
        line = f"{label:<32s}"
        for v in values:
            line += f"{v:>{col_w}s}"
        print(line)

    _row(
        "Fan changes/hour",
        [f"{m.fan_changes / max(total_hours, 0.01):.2f}" for m in metrics_list],
    )
    _row("Total fan changes", [str(m.fan_changes) for m in metrics_list])
    _row(
        "Agreement with live (%)",
        [f"{100.0 * m.agree_with_live / max(m.active_rows, 1):.1f}" for m in metrics_list],
    )
    _row(
        "Avg MPC cost",
        [f"{sum(m.costs) / max(len(m.costs), 1):.2f}" for m in metrics_list],
    )
    _row(
        "Prediction MAE T+10 (C)",
        [
            f"{sum(m.prediction_errors_10m) / len(m.prediction_errors_10m):.3f}"
            if m.prediction_errors_10m
            else "n/a"
            for m in metrics_list
        ],
    )
    _row("Active rows", [str(m.active_rows) for m in metrics_list])
    _row("Disturbed/idle rows", [str(m.total_rows - m.active_rows) for m in metrics_list])

    # Fan distribution
    print(f"\n{'--- Fan Distribution (%) ---':<32s}")
    for fm in fan_modes:
        _row(
            f"  {fm}",
            [
                f"{100.0 * m.fan_dist.get(fm, 0) / max(sum(m.fan_dist.values()), 1):.1f}"
                for m in metrics_list
            ],
        )

    # Overrides summary
    print(f"\n--- Cost Overrides ---")
    for m in metrics_list:
        if m.overrides:
            parts = ", ".join(f"{k}={v}" for k, v in m.overrides.items())
            print(f"  {m.name}: {parts}")
        else:
            print(f"  {m.name}: (defaults)")


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------
def write_output_csv(
    path: str,
    rows: list[Row],
    variants: list[tuple[str, dict[str, float]]],
    all_results: dict[str, list[tuple[dict, str]]],
) -> None:
    """Write per-row replay decisions for all variants to a CSV."""
    variant_names = [name for name, _ in variants]
    header = ["timestamp", "hvac_mode", "current_temp", "target_temp", "vtherm_slope", "live_fan"]
    for vn in variant_names:
        header.extend([f"{vn}_fan", f"{vn}_cost", f"{vn}_status", f"{vn}_would_change"])

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for i, row in enumerate(rows):
            out: list[object] = [
                row.timestamp,
                row.hvac_mode,
                row.current_temp,
                row.target_temp,
                row.vtherm_slope,
                row.decided_fan,
            ]
            for vn in variant_names:
                payload, sim_fan = all_results[vn][i]
                cost = payload.get("mpc_cost")
                out.extend([
                    sim_fan,
                    f"{cost:.3f}" if cost is not None else "",
                    payload.get("mpc_status", ""),
                    payload.get("mpc_would_change_now", "no"),
                ])
            writer.writerow(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_variant_spec(spec: str) -> tuple[str, dict[str, float]]:
    """Parse 'name:KEY=val,KEY=val' into (name, {overrides})."""
    if ":" not in spec:
        return spec, {}
    name, params = spec.split(":", 1)
    overrides: dict[str, float] = {}
    for part in params.split(","):
        key, val = part.split("=", 1)
        key = key.strip()
        if key not in TUNABLE:
            raise argparse.ArgumentTypeError(
                f"Unknown tunable: {key}. Valid: {', '.join(TUNABLE)}"
            )
        overrides[key] = float(val)
    return name, overrides


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Replay CSV traces through the MPC controller with configurable cost weights.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Variant syntax:\n"
            "  --variant NAME               Use default cost weights\n"
            "  --variant NAME:KEY=VAL,...    Override specific constants\n\n"
            "Tunable constants (defaults):\n"
            + "\n".join(f"  {k} = {v}" for k, v in _DEFAULTS.items())
        ),
    )
    parser.add_argument("csv_file", help="Path to a data_collection CSV file")
    parser.add_argument(
        "--variant",
        action="append",
        default=[],
        metavar="SPEC",
        help="Variant specification (repeatable). Format: NAME[:KEY=VAL,...]",
    )
    parser.add_argument("--deadband", type=float, default=0.2, help="Deadband in degrees (default: 0.2)")
    parser.add_argument("--min-interval", type=int, default=10, help="Min interval in minutes (default: 10)")
    parser.add_argument(
        "--fan-order",
        help="Comma-separated fan modes, weakest to strongest (auto-detected if omitted)",
    )
    parser.add_argument(
        "--output",
        help="Write per-row replay decisions to this CSV path",
    )
    args = parser.parse_args()

    # Parse variants
    variants: list[tuple[str, dict[str, float]]] = []
    if not args.variant:
        variants.append(("baseline", {}))
    for spec in args.variant:
        try:
            variants.append(parse_variant_spec(spec))
        except (ValueError, argparse.ArgumentTypeError) as exc:
            parser.error(str(exc))

    # Load data
    print(f"Loading {args.csv_file}...")
    rows = load_csv(args.csv_file)
    if not rows:
        print("Error: no valid rows found.", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(rows)} rows loaded ({rows[0].timestamp} -> {rows[-1].timestamp})")

    # Fan modes
    if args.fan_order:
        fan_modes = [m.strip() for m in args.fan_order.split(",")]
    else:
        fan_modes = detect_fan_modes(rows)
    print(f"  Fan modes: {fan_modes}")

    # Build learning
    print("Building learning profiles...")
    learning = build_learning(rows)
    print(f"  {learning.slope_sample_count()} slope samples ingested")

    # Run variants
    all_metrics: list[Metrics] = []
    all_results: dict[str, list[tuple[dict, str]]] = {}

    for name, overrides in variants:
        label = name
        if overrides:
            label += " (" + ", ".join(f"{k}={v}" for k, v in overrides.items()) + ")"
        print(f"Replaying variant '{label}'...")
        results = replay(rows, learning, fan_modes, overrides, args.deadband, args.min_interval)
        m = compute_metrics(name, overrides, rows, results, args.deadband)
        all_metrics.append(m)
        all_results[name] = results

    # Report
    print_report(rows, fan_modes, learning, all_metrics, args.deadband)

    # Optional CSV output
    if args.output:
        write_output_csv(args.output, rows, variants, all_results)
        print(f"\nPer-row decisions written to {args.output}")


if __name__ == "__main__":
    main()
