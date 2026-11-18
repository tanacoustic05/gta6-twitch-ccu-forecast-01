# Twitch CCU launch forecaster

A small offline Python program that predicts the first-day concurrent-viewer
curve of a newly launched Twitch channel. Given one early print (the viewer
count at hour 1) it returns five quantiles for each hour from 2 through 24.

## How it works

The forecast is built from *comparable launches* — earlier channels whose
first-day curves are already known. For each comparable the program divides
every hourly sample by that channel's own hour-1 value, which turns each curve
into a scale-free shape. Comparables marked as not being launches are ignored,
and a comparable must cover at least 15 of the hours 2–24 with a positive
hour-1 sample to be used at all.

For every target hour the ratios from all usable comparables are sorted and
reduced to a median, a minimum and a maximum. The outer quantiles blend the
median with the extremes in log space (70% median, 30% tail), while the inner
two use a fixed log-normal spread of sigma 0.45 around the median. The five
ratios are then multiplied by the case's hour-1 count to give viewer numbers.

Output is always clamped to at least 1.0, ordered low to high within an hour,
and never allowed to drop below the previous hour, so the curve stays sane even
when the comparable set is thin or noisy.

## Running it

```
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python main.py
```

Both variables default to `/input` and `/output`. Every `*.in` file in the
input directory (a JSON case with the `makefun/twitch-ccu-case/v1` schema) is
read in name order and written to `out/<stem>.out` as a single JSON line with
an `hours` array of 23 entries, each holding `hour` and its five `q` values.

The program uses only the standard library, never touches the network, and is
fully deterministic — the same inputs always produce byte-identical output.

## License

MIT.
