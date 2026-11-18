"""Offline hour-by-hour retention forecaster.

The script reads benchmark cases describing a release checkpoint and a set
of comparable titles, then projects the viewer distribution for hours 2-24
of the target title.  Everything is deterministic and uses only the Python
standard library.
"""

import json
import math
import os
from pathlib import Path

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/output"

MAX_LAG = 23          # hours 2..24 correspond to retention lags 1..23
SCAN_LIMIT = 72       # how many early samples are searched for the unlock
MIN_WEIGHT = 0.05     # comparables further away than this are ignored
RATIO_FLOOR = 1.0 / 50.0
RATIO_CEIL = 2.0
EARLY_CAP = 0.90      # hour 2 keeps at least a 10% drop from the hour-1 peak
LATE_WIDEN = 0.015    # each late hour stretches the quantile band a bit more


def _days_from_civil(year, month, day):
    """Return the number of days between 1970-01-01 and the given date."""
    year -= 1 if month <= 2 else 0
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    doy = (153 * (month - 3 if month > 2 else month + 9) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _parse_stamp(text):
    """Split an ISO timestamp into (whole days, seconds within the day)."""
    raw = str(text).strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    if "T" in raw:
        date_part, _, time_part = raw.partition("T")
    else:
        date_part, _, time_part = raw.partition(" ")
    y, m, d = (int(piece) for piece in date_part.split("-")[:3])
    hour = minute = second = 0
    if time_part:
        time_part = time_part.split("+")[0]
        chunks = time_part.split(":")
        if chunks and chunks[0]:
            hour = int(chunks[0])
        if len(chunks) > 1 and chunks[1]:
            minute = int(chunks[1])
        if len(chunks) > 2 and chunks[2]:
            second = int(float(chunks[2]))
    return _days_from_civil(y, m, d), hour * 3600 + minute * 60 + second


def _hours_between(stamp, origin):
    day, sec = stamp
    return (day - origin[0]) * 24.0 + (sec - origin[1]) / 3600.0


def _viewer_series(comparable):
    """Return (day_start stamp, [(hour index, viewers), ...])."""
    origin = _parse_stamp(comparable.get("day_start"))
    collected = {}
    for sample in comparable.get("hourly") or []:
        try:
            viewers = float(sample.get("viewers"))
            position = _hours_between(_parse_stamp(sample.get("from")), origin)
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        if viewers < 0 or not math.isfinite(viewers):
            continue
        collected[int(round(position))] = viewers
    return origin, sorted(collected.items())


def _find_unlock(points):
    """Locate the steepest early climb in log viewers."""
    window = points[:SCAN_LIMIT]
    first_index = window[0][0]
    best_gain = -1.0
    best_index = first_index
    prev_index, prev_viewers = window[0]
    for index, viewers in window[1:]:
        if index > prev_index and prev_viewers > 0 and viewers > 0:
            gain = (math.log(viewers) - math.log(prev_viewers)) / (index - prev_index)
            if gain > best_gain:
                best_gain = gain
                best_index = index
        prev_index, prev_viewers = index, viewers
    if best_gain <= 0:
        return first_index
    return best_index


def _retention_profile(points, unlock):
    """Ratios viewers(unlock + h) / viewers(unlock) for h in 1..23."""
    baseline = None
    for index, viewers in points:
        if index == unlock:
            baseline = viewers
            break
    if not baseline or baseline <= 0:
        return {}
    profile = {}
    for index, viewers in points:
        lag = index - unlock
        if 1 <= lag <= MAX_LAG:
            profile[lag] = viewers / baseline
    return profile


def _interpolate(profile, lag):
    """Geometric interpolation of a retention profile, clamped."""
    if not profile:
        return 1.0
    if lag in profile:
        value = profile[lag]
    else:
        lags = sorted(profile)
        if lag < lags[0]:
            value = profile[lags[0]]
        elif lag > lags[-1]:
            value = profile[lags[-1]]
        else:
            lower = max(step for step in lags if step < lag)
            upper = min(step for step in lags if step > lag)
            share = (lag - lower) / float(upper - lower)
            left = math.log(max(profile[lower], 1e-9))
            right = math.log(max(profile[upper], 1e-9))
            value = math.exp(left + share * (right - left))
    return min(RATIO_CEIL, max(RATIO_FLOOR, value))


def _weight_for(checkpoint, day_start):
    gap = abs(checkpoint[0] - day_start[0])
    return 1.0 / (1.0 + gap / 30.0)


def _forecast(case):
    try:
        checkpoint = _parse_stamp(case.get("checkpoint"))
    except (TypeError, ValueError, IndexError):
        checkpoint = (0, 0)

    peak = 0.0
    parts = []
    for comparable in case.get("comparables") or []:
        try:
            day_start, points = _viewer_series(comparable)
        except (TypeError, ValueError, IndexError):
            continue
        if not points:
            continue
        peak = max(peak, max(viewers for _, viewers in points))
        weight = _weight_for(checkpoint, day_start)
        if weight < MIN_WEIGHT:
            continue
        unlock = _find_unlock(points)
        profile = _retention_profile(points, unlock)
        if profile:
            parts.append((weight, profile))
    if peak <= 0:
        peak = 1.0

    hours = []
    floor = None
    for hour in range(2, MAX_LAG + 2):
        lag = hour - 1
        if parts:
            numerator = 0.0
            denominator = 0.0
            for weight, profile in parts:
                numerator += weight * math.log(_interpolate(profile, lag))
                denominator += weight
            median = peak * math.exp(numerator / denominator) if denominator else peak
        else:
            median = peak * math.exp(-0.08 * math.sqrt(lag))
        median = min(median, peak)
        if hour == 2:
            # The release spike peaks at the end of hour 1, so the very first
            # projected hour must already sit below that baseline.
            median = min(median, peak * EARLY_CAP)
        elif hour == 3:
            # Hour 2 compressed the path early, so hour 3 follows it down.
            median = min(median, peak * EARLY_CAP * 0.92)
        if floor is not None and median > floor:
            median = floor
        floor = median

        sigma = 0.15 * math.sqrt(hour)
        if hour > 12:
            # Uncertainty keeps compounding past the first half day.
            sigma += LATE_WIDEN * (hour - 12)
        quantiles = [
            median * math.exp(-1.645 * sigma),
            median * math.exp(-0.674 * sigma),
            median,
            median * math.exp(0.674 * sigma),
            median * math.exp(1.645 * sigma),
        ]
        hours.append({"hour": hour, "q": [round(value, 2) for value in quantiles]})
    return {"hours": hours}


def run():
    input_dir = Path(os.environ.get("MAKEFUN_INPUT_DIR", DEFAULT_INPUT))
    output_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", DEFAULT_OUTPUT))
    output_dir.mkdir(parents=True, exist_ok=True)

    for source in sorted(input_dir.glob("*.in")):
        try:
            with source.open("r", encoding="utf-8") as handle:
                case = json.load(handle)
        except (OSError, ValueError):
            case = {}
        result = _forecast(case)
        target = output_dir / (source.stem + ".out")
        with target.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, separators=(",", ":"))
            handle.write("\n")


if __name__ == "__main__":
    run()
