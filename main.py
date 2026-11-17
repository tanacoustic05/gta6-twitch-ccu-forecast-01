"""gta6-twitch-ccu-forecast-01's entry: comparable-launch decay blended with persistence.

I/O contract (unchanged from the starter): read every *.in under MAKEFUN_INPUT_DIR (else
/input), write <id>.out under MAKEFUN_OUTPUT_DIR (else /output), one row per forecast hour
2..24 with five finite quantiles, non-decreasing, in (0, 1e9].

Model:
  1. p1 = hour 1's print from the case's `known` rows. Every forecast hour is p1 times a
     multiplier, so what we estimate is the shape of the path, not its level.
  2. Each comparable launch is aligned on its own unlock hour, taken to be the biggest
     one-hour log jump in the first 72h of its series (the free-text `unlock` field is
     research prose, not machine-readable, so the data picks the hour).
  3. For each distance d = 0..23 after unlock, m[d] = mean over comparables of
     log(viewers[unlock + d] / viewers[unlock]): the average multiplicative log decay.
     A comparable contributes only where it has data, so short series still inform the
     early hours instead of being dropped whole. The raw curve is then smoothed with a
     1-2-1 filter over neighbouring hours, which damps single-hour noise and fills the
     gaps left at large d by series that ran out. d = 0 stays pinned at 0: it is the
     unlock hour itself, log(base / base).
  4. The median path for the target case is p1 * exp(w(d) * m[d]) with
     w(d) = d / (d + BLEND_K): at distance 0 it is pure persistence, and the comparable
     curve takes over as d grows.
  5. Spread is two log-space terms added in quadrature: the random walk of the hourly
     print, SIGMA * sqrt(d), and the uncertainty of the decay estimate itself,
     w(d) * s[d], where s[d] is the spread of comparables' log ratios around m[d].
     So q_tau(h) = median(h) * exp(z_tau * sigma(h)). The five quantiles are sorted so
     the row is non-decreasing no matter what the curve does, then clamped to
     [Q_FLOOR, Q_CEIL].
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
# Floor on the per-distance dispersion of comparable log ratios, so a curve estimated
# from one or two comparables still admits it could be wrong.
SPREAD_FLOOR = 0.05
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


def smooth_121(xs: list[float], pin_first: bool = True) -> list[float]:
    """1-2-1 smoothing across neighbouring hours, renormalised at the ends.

    The raw per-distance averages are noisy: one comparable having an odd hour drags the
    estimate at that distance alone, and distance d can be estimated from fewer
    comparables than d-1 and d+1. A [1, 2, 1] kernel spreads each observation over its
    neighbours, so the curve moves in ones and twos instead of jumping. Index 0 is the
    unlock hour, where the log ratio is exactly 0 by construction; leave it alone.
    """
    if not xs:
        return []
    out = []
    for i in range(len(xs)):
        if pin_first and i == 0:
            out.append(xs[0])
            continue
        total = weight = 0.0
        for j, wj in ((i - 1, 1.0), (i, 2.0), (i + 1, 1.0)):
            if 0 <= j < len(xs):
                total += wj * xs[j]
                weight += wj
        out.append(total / weight)
    return out


def comparable_curves(comparables: list) -> tuple[list[float], list[float]]:
    """Return (m, s) over d = 0..23: the mean log decay and its dispersion.

    m[d] = mean over comparables of log(viewers[unlock + d] / viewers[unlock]).
    s[d] = their sample standard deviation; 0 when only one comparable reaches d.
    A comparable contributes at distance d only if it has data there, so a short series
    still informs the early hours rather than being dropped whole. If nothing is usable,
    the curve is flat at 0 (pure persistence) with no extra spread.
    """
    samples: list[list[float]] = [[] for _ in range(DECAY_HORIZON)]
    for comp in comparables:
        hourly = comp.get("hourly") or []
        viewers = []
        for row in hourly:
            try:
                viewers.append(max(1, int(row.get("viewers", 0))))
            except (TypeError, ValueError):
                viewers.append(1)
        if len(viewers) < 2:
            continue
        # Align on the biggest one-hour log jump within the spike window: the unlock.
        spike, best = 0, 0.0
        for i in range(1, min(SPIKE_WINDOW, len(viewers))):
            if viewers[i - 1] >= 1 and viewers[i] > viewers[i - 1]:
                jump = math.log(viewers[i] / viewers[i - 1])
                if jump > best:
                    best, spike = jump, i
        base = viewers[spike]
        for d in range(DECAY_HORIZON):
            j = spike + d
            if j >= len(viewers):
                break
            samples[d].append(math.log(max(1, viewers[j]) / base))

    m, s = [], []
    for d in range(DECAY_HORIZON):
        xs = samples[d]
        if not xs:
            m.append(0.0)
            s.append(0.0)
            continue
        mu = sum(xs) / len(xs)
        m.append(mu)
        if len(xs) < 2:
            s.append(0.0)
        else:
            var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
            s.append(max(SPREAD_FLOOR, math.sqrt(var)))
    return smooth_121(m), smooth_121(s)


def hour_quantiles(p1: float, d: int, curve: list[float], spread: list[float]) -> list[float | int]:
    """Hour d+1's five quantiles: the blended decay curve widened by its own error."""
    w = d / (d + BLEND_K)
    m = curve[min(d, len(curve) - 1)] if curve else 0.0
    s = spread[min(d, len(spread) - 1)] if spread else 0.0
    median = p1 * math.exp(w * m)
    # Two independent risks in quadrature: hour-to-hour wander, and being wrong about
    # where the comparable curve lands. The second scales with w, since it only bites
    # as far as the curve drives the median.
    sigma = math.sqrt(SIGMA**2 * d + (w * s) ** 2)
    raw = [median * math.exp(z * sigma) for z in Z]
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
    curve, spread = comparable_curves(case.get("comparables") or [])
    return {
        "hours": [
            {"hour": row["hour"], "q": hour_quantiles(p1, row["hour"] - 1, curve, spread)}
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
