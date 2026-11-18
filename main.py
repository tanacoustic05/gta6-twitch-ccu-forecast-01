"""Deterministic offline forecaster for first-day launch viewer curves.

Each case is compared with a set of earlier comparable launches.  For every
comparable we build a normalised curve (viewers at hour h divided by the
viewers at hour 1), take the median curve across comparables, widen it with
two different spread rules, and scale the result back up by the case's own
hour-1 print.
"""

import json
import math
import os
from pathlib import Path

TAIL_WEIGHT = 0.30
CORE_WEIGHT = 0.70
SPREAD_SIGMA = 0.45
SPREAD_Z = 0.6745
FIRST_HOUR = 2
LAST_HOUR = 24
MIN_COVERED_HOURS = 15
NOT_A_LAUNCH = "not a launch"
MIN_VALUE = 1.0

COMPARABLE_KEYS = ("comparables", "comparable_launches", "launches", "comparisons")
UNLOCK_KEYS = ("unlock", "unlock_descriptor", "descriptor", "unlock_text")
POINT_KEYS = ("hourly", "points", "samples", "observations")


def env_path(name, fallback):
    raw = os.environ.get(name)
    return Path(raw) if raw else Path(fallback)


def as_text(value):
    """Flatten an arbitrary JSON value into searchable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(as_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(as_text(item) for item in value)
    return str(value)


def as_number(value):
    """Return a float for numeric JSON values, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def date_part(stamp):
    if not isinstance(stamp, str):
        return ""
    for marker in ("T", " "):
        cut = stamp.find(marker)
        if cut >= 0:
            return stamp[:cut]
    return stamp


def hour_of(stamp):
    if not isinstance(stamp, str):
        return None
    cut = stamp.find("T")
    if cut < 0:
        cut = stamp.find(" ")
    if cut < 0:
        return None
    chunk = stamp[cut + 1:cut + 3]
    if len(chunk) != 2 or not chunk.isdigit():
        return None
    return int(chunk)


def case_base(case):
    """Hour-1 print count for the case, with a history fallback."""
    known = case.get("known")
    if isinstance(known, list) and known:
        head = known[0]
        if isinstance(head, dict):
            value = as_number(head.get("viewers"))
            if value is not None:
                return value
    history = case.get("history")
    if isinstance(history, list):
        for point in reversed(history):
            if isinstance(point, dict):
                value = as_number(point.get("viewers"))
                if value is not None:
                    return value
    return None


def comparable_list(case):
    for key in COMPARABLE_KEYS:
        value = case.get(key)
        if isinstance(value, list):
            return value
    return []


def unlock_description(launch):
    for key in UNLOCK_KEYS:
        if key in launch:
            return as_text(launch[key])
    return ""


def sampled_points(launch):
    for key in POINT_KEYS:
        value = launch.get(key)
        if isinstance(value, list):
            return value
    return []


def ratio_curve(launch):
    """Normalised hour -> ratio map for one comparable, or None if unusable."""
    day_start = launch.get("day_start")
    if day_start is None:
        day_start = launch.get("dayStart")
    day0 = date_part(day_start)
    if not day0:
        return None

    samples = {}
    for point in sampled_points(launch):
        if not isinstance(point, dict):
            continue
        stamp = point.get("from")
        if not isinstance(stamp, str) or not stamp.startswith(day0):
            continue
        hour = hour_of(stamp)
        viewers = as_number(point.get("viewers"))
        if hour is None or viewers is None:
            continue
        samples[hour] = viewers

    base = samples.get(1, 0.0)
    if base <= 0.0:
        return None
    covered = sum(1 for hour in range(FIRST_HOUR, LAST_HOUR + 1) if hour in samples)
    if covered < MIN_COVERED_HOURS:
        return None

    return {hour: viewers / base for hour, viewers in samples.items()
            if hour >= 1 and viewers > 0.0}


def median(values):
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def quantile_row(ratios, base):
    middle = median(ratios)
    low_ratio = middle ** CORE_WEIGHT * ratios[0] ** TAIL_WEIGHT
    high_ratio = middle ** CORE_WEIGHT * ratios[-1] ** TAIL_WEIGHT
    low_inner = middle * math.exp(-SPREAD_Z * SPREAD_SIGMA)
    high_inner = middle * math.exp(SPREAD_Z * SPREAD_SIGMA)
    ratios_out = (low_ratio, low_inner, middle, high_inner, high_ratio)
    return [ratio * base for ratio in ratios_out]


def tidy_row(values, floor_row):
    """Clamp to a sane minimum, order low-to-high, and never dip below the
    previous hour's corresponding value."""
    row = []
    running = MIN_VALUE
    for index, value in enumerate(values):
        value = max(value, MIN_VALUE)
        if floor_row is not None:
            value = max(value, floor_row[index])
        running = max(running, value)
        row.append(running)
    return row


def forecast(case):
    base = case_base(case)
    if base is None:
        base = 0.0

    curves = []
    for launch in comparable_list(case):
        if not isinstance(launch, dict):
            continue
        if NOT_A_LAUNCH in unlock_description(launch).lower():
            continue
        curve = ratio_curve(launch)
        if curve is not None:
            curves.append(curve)

    rows = []
    carried = None
    previous = None
    for hour in range(FIRST_HOUR, LAST_HOUR + 1):
        values = sorted(curve[hour] for curve in curves if hour in curve)
        if values:
            carried = values
        elif carried is not None:
            values = carried
        else:
            values = [1.0]
        row = tidy_row(quantile_row(values, base), previous)
        rows.append({"hour": hour, "q": [round(value, 3) for value in row]})
        previous = row
    return rows


def main():
    input_dir = env_path("MAKEFUN_INPUT_DIR", "/input")
    output_dir = env_path("MAKEFUN_OUTPUT_DIR", "/output")
    output_dir.mkdir(parents=True, exist_ok=True)

    for source in sorted(input_dir.glob("*.in"), key=lambda path: path.name):
        with source.open("r", encoding="utf-8") as handle:
            case = json.load(handle)
        payload = {"hours": forecast(case)}
        target = output_dir / (source.stem + ".out")
        with target.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
