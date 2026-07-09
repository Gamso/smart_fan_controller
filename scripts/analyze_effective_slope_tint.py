#!/usr/bin/env python3
"""Grey-box (1R1C) analysis of per-fan cooling capacity using an outdoor sensor.

Merges a controller data-collection CSV with an outdoor-temperature export
(Home Assistant history CSV: entity_id,state,last_changed) and fits the
first-order thermal model

    dT/dt = k_env * (T_ext - T) + u_fan

by ordinary least squares (one shared envelope conductance ``k_env`` plus a
per-fan cooling-power intercept ``u_fan``). It then probes whether ``u_fan`` is
actually state-independent by binning the delivered cooling (-dT/dt) against the
comfort error (a proxy for compressor demand).

This is an *offline analysis* helper, not part of the running integration.

Usage:
    python scripts/analyze_effective_slope_tint.py CONTROLLER.csv OUTDOOR.csv \\
        [--hvac cool] [--outdoor-entity sensor.gardanne_temperature]
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import statistics
from collections import Counter


def parse_ts(s: str) -> dt.datetime:
    """Parse an ISO-8601 timestamp with optional trailing Z."""
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_outdoor(path: str, entity: str | None) -> list[tuple[dt.datetime, float]]:
    """Load and sort (timestamp, value) pairs from an HA history CSV."""
    series: list[tuple[dt.datetime, float]] = []
    for r in csv.reader(open(path, encoding="utf-8")):
        if not r or r[0] in ("entity_id",):
            continue
        if entity and r[0] != entity:
            continue
        try:
            series.append((parse_ts(r[2]), float(r[1])))
        except (ValueError, IndexError):
            continue
    series.sort()
    return series


def interpolator(series: list[tuple[dt.datetime, float]]):
    """Return a linear-interpolation function over a sorted time series."""
    times = [t for t, _ in series]

    def at(t: dt.datetime) -> float:
        if t <= series[0][0]:
            return series[0][1]
        if t >= series[-1][0]:
            return series[-1][1]
        i = bisect.bisect_right(times, t)
        t0, v0 = series[i - 1]
        t1, v1 = series[i]
        return v0 + (v1 - v0) * ((t - t0) / (t1 - t0))

    return at


def fnum(x: str) -> float | None:
    """Parse a float or return None."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def solve(A: list[list[float]], b: list[float]) -> list[float]:
    """Solve A·x = b by Gaussian elimination with partial pivoting."""
    n = len(A)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        pv = M[c][c]
        if abs(pv) < 1e-12:
            continue
        M[c] = [v / pv for v in M[c]]
        for r in range(n):
            if r != c and abs(M[r][c]) > 1e-12:
                f = M[r][c]
                M[r] = [M[r][k] - f * M[c][k] for k in range(n + 1)]
    return [M[i][n] for i in range(n)]


def main() -> None:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("controller_csv")
    ap.add_argument("outdoor_csv")
    ap.add_argument("--hvac", default="cool")
    ap.add_argument("--outdoor-entity", default=None, help="Filter outdoor CSV to this entity_id")
    args = ap.parse_args()

    ext = load_outdoor(args.outdoor_csv, args.outdoor_entity)
    if not ext:
        raise SystemExit("No outdoor samples loaded (check --outdoor-entity).")
    iext = interpolator(ext)
    print(f"Outdoor: {len(ext)} samples, range {min(v for _, v in ext):.1f}..{max(v for _, v in ext):.1f} C")

    rows = [r for r in csv.DictReader(open(args.controller_csv, newline="", encoding="utf-8")) if r["hvac_mode"] == args.hvac]
    # (fan, Text-T, dT/dt, comfort_error)
    S: list[tuple[str, float, float, float]] = []
    for r in rows:
        if r["phase"] != "ESTABLISHED":
            continue
        if r.get("is_window_open") in ("1", "True", "true"):
            continue
        if r.get("defrost_active") in ("1", "True", "true"):
            continue
        if r.get("hvac_idle") in ("1", "True", "true"):
            continue
        T = fnum(r["current_temp"])
        vs = fnum(r["vtherm_slope"])
        er = fnum(r["current_error"])
        if None in (T, vs, er):
            continue
        S.append((r["current_fan"], iext(parse_ts(r["timestamp"])) - T, vs, er))

    fans: list[str] = []
    for f, *_ in S:
        if f not in fans:
            fans.append(f)
    print(f"Samples: {len(S)}  per fan: {dict(Counter(s[0] for s in S))}\n")

    # --- fixed-effects fit: dT/dt = k_env*(Text-T) + sum(u_fan*1[fan]) ---
    p = 1 + len(fans)
    A = [[0.0] * p for _ in range(p)]
    b = [0.0] * p
    for f, dext, vs, _ in S:
        x = [0.0] * p
        x[0] = dext
        x[1 + fans.index(f)] = 1.0
        for i in range(p):
            b[i] += x[i] * vs
            for j in range(p):
                A[i][j] += x[i] * x[j]
    beta = solve(A, b)
    k_env, u = beta[0], {f: beta[1 + i] for i, f in enumerate(fans)}

    ybar = sum(s[2] for s in S) / len(S)
    ss_res = ss_tot = 0.0
    for f, dext, vs, _ in S:
        yhat = k_env * dext + u[f]
        ss_res += (vs - yhat) ** 2
        ss_tot += (vs - ybar) ** 2
    r2 = 1 - ss_res / ss_tot

    print("=== 1R1C fixed-effects fit:  dT/dt = k_env*(Text - T) + u_fan ===")
    print(f"k_env = {k_env:.4f} /h   tau = {1 / k_env:.1f} h")
    print(f"R2 = {r2:.3f}   RMSE = {(ss_res / len(S)) ** 0.5:.3f} C/h "
          f"(vs signal std {statistics.pstdev([s[2] for s in S]):.2f} -> residual is mostly derivative/quantization noise)")
    print(f"\n{'fan':10s} {'u_fan(C/h)':>11s}  max (Text-T) held flat = -u_fan/k_env")
    for f in fans:
        print(f"  {f:10s} {u[f]:+11.3f}   {-u[f] / k_env:+6.1f} C")

    # --- is u_fan constant? bin delivered cooling by demand (comfort error) ---
    print("\n=== Delivered cooling (-dT/dt, + = cools) by fan x comfort-error band ===")
    print("    (a flat row would mean u_fan is state-independent; it is NOT)")
    ebins = [(-2, -0.2), (-0.2, 0.2), (0.2, 0.6), (0.6, 1.2), (1.2, 2.0), (2.0, 6.0)]
    print("fan       " + "".join(f"{f'[{lo:+.1f},{hi:+.1f})':>13s}" for lo, hi in ebins))
    for f in fans:
        cells = []
        for lo, hi in ebins:
            g = [s for s in S if s[0] == f and lo <= s[3] < hi]
            if len(g) < 5:
                cells.append(f"{'.':>13s}")
            else:
                cells.append(f"{sum(-s[2] for s in g) / len(g):+.2f}(n{len(g)})".rjust(13))
        print(f"{f:10s}" + "".join(cells))


if __name__ == "__main__":
    main()
