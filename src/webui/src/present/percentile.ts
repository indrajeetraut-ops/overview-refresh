/**
 * Layout helpers for `PercentileBar` (charts.tsx). Real PR cost / time-to-merge distributions are
 * heavily right-skewed (min and p50 a few dollars apart, p95 25× the median), so on a linear
 * track the low marks land on top of each other and their labels collide.
 */

/** Above this max/min ratio the track switches to a log scale. */
export const LOG_SCALE_RATIO = 20;

/** `v → 0..100` position on the track. Log when the spread is wide and every value is positive
 * (a zero/negative min can't be logged — stays linear). */
export function percentileScale(min: number, max: number): { pos: (v: number) => number; log: boolean } {
  const log = min > 0 && max / min > LOG_SCALE_RATIO;
  const f = log ? Math.log : (v: number) => v;
  const lo = f(min);
  const span = f(max) - lo || 1;
  const clamp = (x: number) => Math.min(100, Math.max(0, x));
  return { pos: (v: number) => clamp(((f(log ? Math.max(v, min) : v) - lo) / span) * 100), log };
}

/**
 * Greedy lane assignment so no two labels overlap. `items` are `{ center, width }` in px, in
 * track order. Each label is clamped inside `[0, trackWidth]`, then dropped into the first lane
 * whose previous label ends at least `gap` px before it starts. Returns each label's `left` (px)
 * and `lane` (0 = nearest the track), plus the lane count.
 */
export function assignLabelLanes(
  items: { center: number; width: number }[], trackWidth: number, gap = 8,
): { placed: { left: number; lane: number }[]; lanes: number } {
  const laneEnds: number[] = [];
  const placed = items.map(({ center, width }) => {
    const left = Math.min(Math.max(center - width / 2, 0), Math.max(trackWidth - width, 0));
    let lane = laneEnds.findIndex((end) => left >= end + gap);
    if (lane === -1) { lane = laneEnds.length; laneEnds.push(0); }
    laneEnds[lane] = left + width;
    return { left, lane };
  });
  return { placed, lanes: Math.max(laneEnds.length, 1) };
}
