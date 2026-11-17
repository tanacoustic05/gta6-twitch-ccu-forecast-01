"""gta6-twitch-ccu-forecast-01's entry: comparable-launch decay blended with persistence.

I/O contract (unchanged from the starter): read every *.in under MAKEFUN_INPUT_DIR (else
/input), write <id>.out under MAKEFUN_OUTPUT_DIR (else /output), one row per forecast hour
2..24 with five finite quantiles, non-decreasing, in (0, 1e9].

Model:
  1. p1 = hour 1's print from the case's `known` rows.
  2. Each comparable launch is aligned on its own unlock hour, detected in the data as the
     biggest one-hour log jump in viewers within the first 72h of its series (the free-text
     `unlock` field is research prose, not machine-readable, so the data picks the hour).
  3. For each delta d = 0..23 after unlock, m[d] = mean over comparables of
     log(viewers[unlock + d] / viewers[unlock]): the average multiplicative log decay.
  4. The median path for the target case is p1 * exp(w(d) * m[d]) with w(d) = d / (d + 4):
     at hour distance 0 it is pure p1, and the comparable curve takes over as d grows.
  5. Spread is the starter's random walk in log space, sigma = 0.15 per sqrt-hour:
     q_tau(h) = median(h) * exp(z_tau * 0.15 * sqrt(h - 1)). The five quantiles are sorted
     so the row is non-decreasing no matter what the curve does, then clamped to (0, 1e9].
     Tweaks in the second session: BLEND_K retuned from 4.0 to 3.0 after seeing the practice
     outputs, and spike search window widened from 48 to 72 hours.
"""

import json
import math
import os
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# The quantile levels the contest scores, in the order each row lists them.
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# The standard normal's quantile at each level.
Z = [-1.645, -0.674, 0, 0.674, 1.645]
# Spread of an hour's move, in natural-log units per square-root hour.
SIGMA = 0.15
# How quickly the comparable decay curve takes over from pure persistence:
# w(d) = d / (d + BLEND_K), so hour distance d=4 gets half the curve, d=24 about 86%.
BLEND_K = 3.0
# Only the first 24 hours after a comparable's unlock inform the decay curve.
DECAY_HORIZON = 24
# Spike detection searches the first 72h of a comparable's series.
SPIKE_WINDOW = 72
# Quantile floor/ceiling applied after rounding, per the output contract.
Q_FLOOR = 0.01
Q_CEIL = 1e9


def round2(x: float) -> float | int:
    """x rounded to 2 decimals, half up, as JavaScript's Math.round(x * 100) / 100 rounds it."""
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value


def mean_log_decay_curve(comparables: list) -> list[float]:
    """m[d] = mean over comparables of log(viewers[unlock + d] / viewers[unlock]), d = 0..23.

    A comparable contributes to d only if it has data at unlock + d; comparables whose
    series is too short simply drop out of the average for large d. If nothing is usable,
    the curve is flat at 0 (pure persistence).
    """
    sums = [0.0] * DECAY_HORIZON
    counts = [0] * DECAY_HORIZON
    for comp in comparables:
        hourly = comp.get("hourly") or []
        if len(hourly) < DECAY_HORIZON + 1:
            continue
        viewers = []
        for row in hourly:
            try:
                viewers.append(max(1, int(row.get("viewers", 0))))
            except (TypeError, ValueError):
                viewers.append(1)
        # Align on the biggest one-hour log jump within the spike window: the unlock.
        v0 = viewers[0]
        spike, best = 0, 0.0
        for i in range(1, min(SPIKE_WINDOW, len(viewers))):
            if viewers[i - 1] >= 1 and viewers[i] > viewers[i - 1]:
                jump = math.log(viewers[i] / viewers[i - 1])
                if jump > best:
                    best, spike = jump, i
        base = viewers[spike]
        if base < 1:
            continue
        for d in range(DECAY_HORIZON):
            j = spike + d
            if j >= len(viewers):
                break
            sums[d] += math.log(max(1, viewers[j]) / base)
            counts[d] += 1
    return [sums[d] / counts[d] if counts[d] else 0.0 for d in range(DECAY_HORIZON)]


def hour_quantiles(p1: float, d: int, curve: list[float]) -> list[float | int]:
    """Hour d+1's five quantiles: the blended decay curve widened by the random walk."""
    w = d / (d + BLEND_K)
    m = curve[min(d, len(curve) - 1)] if curve else 0.0
    median = p1 * math.exp(w * m)
    raw = [median * math.exp(z * SIGMA * math.sqrt(d)) for z in Z]
    qs = sorted(round2(x) for x in raw)
    qs = [min(max(q, Q_FLOOR), Q_CEIL) for q in qs]
    # round2 is monotone, and sorting precedes clamping, so this running max only ever
    # re-asserts the order the sort already chose; kept as a safety net for the contract.
    for i in range(1, len(qs)):
        if qs[i] < qs[i - 1]:
            qs[i] = qs[i - 1]
    return qs


def forecast_case(case: dict, name: str) -> dict:
    if case.get("levels") != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    first = next((k for k in case.get("known", []) if k.get("hour") == 1), None)
    if first is None:
        raise ValueError(f"{name} has no hour-1 print")
    p1 = max(1, first["viewers"])
    curve = mean_log_decay_curve(case.get("comparables") or [])
    return {
        "hours": [
            {"hour": row["hour"], "q": hour_quantiles(p1, row["hour"] - 1, curve)}
            for row in case["forecast"]
        ]
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
