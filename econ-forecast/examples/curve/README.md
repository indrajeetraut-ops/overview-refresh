# Curve examples

Worked examples for the curve-creation tool: each **config** here and the exact **artifact**
it produced under `artifacts/`. Open a `preview.html` in a browser (or `preview.png`) to see
the shape. First time here? Read [the module design](../../docs/design/curve-creation-design.md)
or just run the commands below.

## The examples

| Config | `name:id` | Shape |
|---|---|---|
| [`ramp-and-plateau.yaml`](ramp-and-plateau.yaml) | `ramp-and-plateau:334e67bdc9b546bb` | slow ramp, then a plateau |
| [`steady-decline.yaml`](steady-decline.yaml) | `steady-decline:b6a5ed80fa49f7eb` | consumption tapering off |
| [`s-curve-growth.yaml`](s-curve-growth.yaml) | `s-curve-growth:ea9d6006276dfb97` | adoption S-curve: slow, steep, plateau |
| [`growth-with-launch-spike.yaml`](growth-with-launch-spike.yaml) | `growth-with-launch-spike:26fd0714baf6c037` | gentle ramp, a sharp launch spike (~5×), then settles and continues |

Between them they show the whole config surface: absolute (`"3.6"`) vs. delta (`"+1.5"`,
`"-1.0"`) anchor values, different abstract axes (`1..12`, `1..10`, `1..20`, `1..30`), and
different `preview.days`/`baseline_tokens`. `growth-with-launch-spike` shows a **deterministic
spike carried by the curve** — used by [`../synthetic/curve-spiked.yaml`](../synthetic/curve-spiked.yaml);
it appears cleanly in generated data but (being one-off) does not reproduce in a forecast horizon
(for that, declare a `known_events` spike on the forecast — see [`../known-event-spike`](../known-event-spike)).

## Reading the layout — what `name:id` means

Each run writes to `artifacts/<name>/<config-hash>/`:

```
artifacts/
  ramp-and-plateau/            <- name (from the config's `name:` field)
    334e67bdc9b546bb/          <- id = short sha256 of the resolved config
      config.yaml              resolved input (provenance)
      curve.jsonl              sampled {pos, level} rows (inspection/preview only)
      preview.html             interactive Plotly chart  <- open this
      preview.png              static chart for sharing
      report.md                anchor table, interp, min/max level, run info
      manifest.json            hashes, versions, run id (written last)
```

The **id is the config hash**, so the same config always lands in the same directory
(idempotent — re-running is a no-op) and *any* change produces a new id. That's why we
address a curve as **`name:id`** (e.g. `ramp-and-plateau:334e67bdc9b546bb`), which resolves
to the path `ramp-and-plateau/334e67bdc9b546bb/`.

## Regenerate these / try your own

```bash
# regenerate one example (writes to the same name:id — a friendly no-op if unchanged)
./econ-forecast/utils/curve.sh \
  --config econ-forecast/examples/curve/ramp-and-plateau.yaml \
  --output econ-forecast/examples/curve/artifacts --open

# check a config without writing anything
./econ-forecast/utils/curve.sh --config econ-forecast/examples/curve/s-curve-growth.yaml --validate-only

# author your own: copy a config, edit the anchors, point --output anywhere
```

The wrapper provisions its virtualenv on first run, so no manual setup is needed. Edit an
anchor and re-run to watch a *new* `name:id` appear next to the originals.
