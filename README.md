# Hourly Viewer Forecaster

A small, dependency-free Python 3.13 program that projects Twitch-style
concurrent-viewer counts for a title release across hours 2 through 24.
Given a checkpoint marking the end of hour 1 and a set of comparable
releases, it produces a median path plus four surrounding quantiles for
every hour, written as one JSON line per case.

## How the blend works

Each comparable's hourly series is rescaled to the hour where its viewer
count jumps hardest — that jump is treated as its own unlock, and anything
earlier is discarded as pre-release noise. What remains becomes a retention
curve relative to the unlock hour. Comparables count more heavily when their
start date is close to the target's checkpoint (weight `1 / (1 + gap/30)`,
dropping anything under `0.05`).

For each horizon the per-comparable retention ratios are geometrically
interpolated, clamped to `[1/50, 2]`, and combined as a weighted geometric
mean. That composite ratio is applied to the target baseline, which is the
largest single viewer value seen anywhere in the comparable data — a proxy
for the release spike at the end of hour 1. Medians are forced downward
across hours, and the spread is modelled as `0.15 * sqrt(hour)`, widening
further past hour 12 so long horizons stay honestly uncertain. Quantiles use
log-normal multipliers, so the bands are asymmetric around the median.

## Running

From the kit root, with `input/` holding the case files:

```bash
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python main.py
```

Both environment variables default to `/input` and `/output`. Every `*.in`
file is read and a matching `*.out` file is written. Output shape:

```json
{"hours":[{"hour":2,"q":[q5,q25,q50,q75,q95]}, ... {"hour":24, ...}]}
```

Twenty-three entries, quantiles rounded to two decimals. No network access,
no randomness, standard library only — the same inputs always give the same
numbers.

## License

MIT.