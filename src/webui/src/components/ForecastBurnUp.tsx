/**
 * The forecast burn-up chart with everything around it that is derived from the run: the rows,
 * the run-out and budget markers, the likely-range label, and the baseline line when another
 * preset is selected. Shared by the Economics forecast panel and the Overview forecast card, so
 * both draw exactly the same chart from the same `present/forecast.ts` helpers. It only lays out.
 */
import { BurnUpChart } from "./charts";
import type { Schemas } from "../data/source";
import { BASELINE_ID, bandLabel, burnUpMarkers, burnUpRows } from "../present/forecast";

export default function ForecastBurnUp({ view, presetId, periodIndex, height }: {
  view: Schemas["ForecastView"];
  presetId: string;
  periodIndex: number;
  /** Chart height; the chart's own default when omitted. */
  height?: number;
}) {
  return (
    <BurnUpChart
      rows={burnUpRows(view, presetId, periodIndex)}
      markers={burnUpMarkers(view, presetId, periodIndex)}
      band={bandLabel(view.band_low, view.band_high)}
      showBaseline={presetId !== BASELINE_ID}
      height={height}
    />
  );
}
