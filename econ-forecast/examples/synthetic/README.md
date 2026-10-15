# Synthetic data examples

Schema-v2 synthetic configs that reuse the maintained users and roster
(`../../artifacts/users`, `../../artifacts/roster`). Each project belongs to exactly one team
(team1: payment/auth; team2: data_pipeline/search; team3: developer_tooling), per the
forecasting structural model. **Weekend reduction is OFF by default** — you opt into a weekly
pattern explicitly.

| File | What it shows | Weekend | Curve / spike |
|---|---|---|---|
| `baseline.yaml` | The plain default: per-profile baselines + daily jitter, no pattern. | off | none |
| `weekend-drop.yaml` | The one example with a weekly pattern. | **on** (`0.6`/`0.25`) | none |
| `curve-spiked.yaml` | A deterministic spike carried by a curve (shows cleanly in the data). | off | `growth-with-launch-spike` (org) |
| `curve-guided.yaml` | Smooth trend curves bound to org / team2 / claude agent. | off | ramp-and-plateau (org), steady-decline (team2), s-curve-growth (claude) |

Verified aggregate behavior (org total, this seed):

| Example | Weekend/weekday mean ratio | Spike |
|---|---:|---|
| `baseline` | 0.993 (≈1.0 → no weekly pattern) | — |
| `weekend-drop` | 0.56 (clear weekly drop) | — |
| `curve-spiked` | ~1.0 | peak/median ≈ 2.69, on the launch day (~2026-07-26) |

## Run
From the repository root, into a fresh output dir (`--force` overwrites an existing one):

```bash
./econ-forecast/utils/synt_gen.sh --op series-create \
  --config econ-forecast/examples/synthetic/baseline.yaml \
  --output /tmp/synthetic-baseline
```

Swap the config for `weekend-drop.yaml`, `curve-spiked.yaml`, or `curve-guided.yaml`.

## Notes
- **Spikes:** a curve-carried spike (`curve-spiked`) is a deterministic feature of the observed
  data and shows cleanly — but a one-off historical spike is **not** reproduced in a forecast
  horizon (see the forecasting design §7/§14). For a planned spike that appears *in the forecast*,
  declare a `known_events` block on the forecast config — see
  [`../known-event-spike`](../known-event-spike).
- **Curves** are the locked artifacts under [`../curve`](../curve); bind them by `name:id`.
- The generator still supports a project spanning multiple teams for realism; these examples use
  one-team-per-project by convention to match the forecasting model.
