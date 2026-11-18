"""Comparable-launch decay forecaster.

Reads case files (*.in) describing a game/content launch: the creator's own
hour-1 viewership baseline ("known") plus a set of comparable launches with
hourly viewership series. For each comparable we locate the "unlock" hour -
the biggest one-hour log jump in viewers within the first 72 entries - then
build an average post-unlock decay/retention curve in log space. The target's
quantile trajectory for hours 2..24 is the hour-1 baseline multiplied by a
smoothed, time-weighted mean log ratio, with spread from the sample std of
those ratios, and each tau quantile is median * exp(z_tau * sigma).

Inputs:  $MAKEFUN_INPUT_DIR  (default /input)  - case files <stem>.in
Outputs: $MAKEFUN_OUTPUT_DIR (default /output) - <stem>.out, one-line JSON
"""

import json
import math
import os
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))


def load_case(path):
    """Load one case file and return its parsed JSON payload."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


MIN_ROWS = 12           # comparables with fewer hourly rows are skipped
SCAN_WINDOW = 72        # unlock jump searched within first 72 hourly entries
HORIZON_D = 23          # offsets d = 0..23 -> hours 1..24
Q_CAP = 1e9
Z = {5: -1.6449, 25: -0.6745, 50: 0.0, 75: 0.6745, 95: 1.6449}


def _viewers(row):
    if isinstance(row, dict):
        for key in ("viewers", "viewership", "views", "value"):
            if key in row:
                return float(row[key])
        raise KeyError("no viewers field in row")
    return float(row)


def _mean(xs):
    return sum(xs) / len(xs)


def _sample_std(xs):
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def _p1(case):
    """Hour-1 viewers print from the case's own 'known' rows."""
    by_hour = {}
    for row in case.get("known") or []:
        if isinstance(row, dict):
            by_hour[int(row.get("hour", 1))] = _viewers(row)
        else:
            by_hour.setdefault(1, _viewers(row))
    if 1 in by_hour:
        return by_hour[1]
    if by_hour:
        return by_hour[min(by_hour)]
    raise ValueError("case has no hour-1 'known' viewers")


def _unlock_hour(series):
    """Index of the biggest one-hour log jump within the first SCAN_WINDOW."""
    best_u, best_jump = None, None
    for u in range(1, min(SCAN_WINDOW, len(series))):
        prev, cur = series[u - 1], series[u]
        if prev <= 0.0 or cur <= 0.0:
            continue
        jump = math.log(cur) - math.log(prev)
        if best_jump is None or jump > best_jump:
            best_u, best_jump = u, jump
    return best_u


def forecast_case(case):
    """Compute the quantile forecast for one case."""
    p1 = _p1(case)
    ratios = {d: [] for d in range(HORIZON_D + 1)}
    for comp in case.get("comparables") or []:
        hourly = comp.get("hourly") if isinstance(comp, dict) else comp
        series = [_viewers(r) for r in (hourly or [])]
        if len(series) < MIN_ROWS:
            continue
        u = _unlock_hour(series)
        if u is None or series[u] <= 0.0:
            continue
        for d in range(HORIZON_D + 1):
            idx = u + d
            if idx < len(series) and series[idx] > 0.0:
                ratios[d].append(math.log(series[idx] / series[u]))

    m = [0.0] + [_mean(ratios[d]) if ratios[d] else 0.0
                 for d in range(1, HORIZON_D + 1)]
    ms = [(m[max(d - 1, 0)] + 2.0 * m[d] + m[min(d + 1, HORIZON_D)]) / 4.0
          for d in range(HORIZON_D + 1)]

    hours = []
    for h in range(2, 25):
        d = h - 1
        w = d / (d + 3.0)
        s = _sample_std(ratios[d]) if ratios[d] else 0.0
        median = p1 * math.exp(w * ms[d])
        sigma = math.sqrt((0.15 * math.sqrt(d)) ** 2 + (w * s) ** 2)
        qs = sorted(round(min(max(median * math.exp(Z[tau] * sigma), 0.0), Q_CAP), 2)
                    for tau in (5, 25, 50, 75, 95))
        hours.append({"hour": h, "q": qs})
    return {"hours": hours}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = load_case(path)
        result = forecast_case(case)
        out_path = OUTPUT_DIR / (path.stem + ".out")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()

Z = {5: -1.6449, 25: -0.6745, 50: 0.0, 75: 0.6745, 95: 1.6449}
HORIZON_D = 23          # offsets d = 0..23 -> hours 1..24
SCAN_WINDOW = 72        # unlock jump searched within first 72 hourly entries
MIN_ROWS = 8            # comparables with fewer hourly rows are skipped
Q_CAP = 1e9


def _viewers(row):
    """Extract the viewers value from a row (dict or bare number)."""
    if isinstance(row, dict):
        for key in ("viewers", "viewership", "views", "value"):
            if key in row:
                return float(row[key])
        raise KeyError("no viewers field in row")
    return float(row)


def _mean(xs):
    return sum(xs) / len(xs)


def _sample_std(xs):
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _p1(case):
    """Hour-1 viewers print from the case's own 'known' rows."""
    rows = case.get("known") or []
    by_hour = {}
    for row in rows:
        if isinstance(row, dict):
            hour = int(row.get("hour", 1))
            by_hour[hour] = _viewers(row)
        else:
            by_hour.setdefault(1, _viewers(row))
    if 1 in by_hour:
        return by_hour[1]
    if by_hour:
        return by_hour[min(by_hour)]
    raise ValueError("case has no hour-1 'known' viewers")


def _unlock_hour(series):
    """Index of the biggest one-hour log jump within the first SCAN_WINDOW."""
    n = min(SCAN_WINDOW, len(series))
    best_u, best_jump = None, None
    for u in range(1, n):
        prev, cur = series[u - 1], series[u]
        if prev <= 0.0 or cur <= 0.0:
            continue
        jump = math.log(cur) - math.log(prev)
        if best_jump is None or jump > best_jump:
            best_u, best_jump = u, jump
    if best_u is None:
        return None
    return best_u


def forecast_case(case):
    p1 = _p1(case)

    # Per-offset log ratios relative to each comparable's unlock hour.
    ratios = {d: [] for d in range(HORIZON_D + 1)}
    for comp in case.get("comparables") or []:
        series = [_viewers(r) for r in (comp.get("hourly") or comp.get("hours") or comp if isinstance(comp, list) else comp.get("hourly") or [])]
        if len(series) < MIN_ROWS:
            continue
        u = _unlock_hour(series)
        if u is None or series[u] <= 0.0 or series[u - 1] <= 0.0:
            continue
        for d in range(HORIZON_D + 1):
            idx = u + d
            if idx < len(series) and series[idx] > 0.0:
                ratios[d].append(math.log(series[idx] / series[u]))

    # Mean log-ratio curve; m[0] is exactly 0 by construction.
    m = [0.0] + [_mean(ratios[d]) if ratios[d] else 0.0 for d in range(1, HORIZON_D + 1)]

    # Smooth m with a 1-2-1 kernel, ends handled by clamping indices.
    ms = []
    for d in range(HORIZON_D + 1):
        lo = m[max(d - 1, 0)]
        hi = m[min(d + 1, HORIZON_D)]
        ms.append((lo + 2.0 * m[d] + hi) / 4.0)

    hours = []
    for h in range(2, 25):
        d = h - 1
        w = d / (d + 3.0)
        s = _sample_std(ratios[d]) if ratios[d] else 0.0
        median = p1 * math.exp(w * ms[d])
        sigma = math.sqrt((0.15 * math.sqrt(d)) ** 2 + (w * s) ** 2)
        qs = []
        for tau in (5, 25, 50, 75, 95):
            q = median * math.exp(Z[tau] * sigma)
            q = min(max(q, 1e-9), Q_CAP) if q <= 0.0 else min(q, Q_CAP)
            qs.append(round(q, 2))
        hours.append({"hour": h, "q": sorted(qs)})
    return {"hours": hours}
