/**
 * Lifecycle-timeline layout (MVP plan U7). The API returns raw milestone timestamps only
 * (`schemas/README.md`'s own strip-rule — no pre-formatted spans or x-positions in data);
 * this is the client-side layout `<LifecycleTimeline>` actually draws from, shared by the
 * Session/PR/Feature deep-dives rather than each re-deriving it.
 */
import { fmtDate, fmtDuration } from "./format";

export type LifecycleMilestone = {
  name: string;
  // `| undefined` alongside `| null`: an optional-with-null-default Pydantic field surfaces
  // as `string | null | undefined` in the generated TS (the property itself is optional),
  // not just `string | null` -- accept both so call sites can pass a schema field straight
  // through without an extra `?? null` at every one.
  ts: string | null | undefined;
  spanKind?: "build" | "review" | "rework";
};
export type PositionedMilestone = LifecycleMilestone & { x: number; spanLabel?: string };

/**
 * Places milestones proportionally along [0, 100] by elapsed real time between the first and
 * last *reached* timestamp. An unreached milestone (`ts === null`, e.g. "merged" still
 * pending) is pinned at `x = 100`, same as the concept mockup's own convention. Each reached
 * span between two consecutive reached milestones gets a `spanLabel` ("Build · 1h 58m") from
 * the elapsed duration, using the *earlier* milestone's own `spanKind`.
 */
export function positionLifecycle(milestones: LifecycleMilestone[]): PositionedMilestone[] {
  // `== null` deliberately catches both `null` (unreached, per the API contract) and
  // `undefined` (an optional field simply omitted) the same way -- both mean "not reached".
  const reachedTimes = milestones
    .filter((m): m is LifecycleMilestone & { ts: string } => m.ts != null)
    .map((m) => new Date(m.ts).getTime());
  const t0 = reachedTimes[0];
  const t1 = reachedTimes[reachedTimes.length - 1];
  const span = t0 != null && t1 != null && t1 > t0 ? t1 - t0 : null;

  const positioned: PositionedMilestone[] = milestones.map((m) => {
    if (m.ts == null) return { ...m, x: 100 };
    if (span == null || t0 == null) return { ...m, x: 0 };
    return { ...m, x: ((new Date(m.ts).getTime() - t0) / span) * 100 };
  });

  const withSpans = positioned.map((m, i) => {
    const next = positioned[i + 1];
    if (!next || m.ts == null || next.ts == null || !m.spanKind) return m;
    const minutes = (new Date(next.ts).getTime() - new Date(m.ts).getTime()) / 60_000;
    const label = m.spanKind[0].toUpperCase() + m.spanKind.slice(1);
    return { ...m, spanLabel: `${label} · ${fmtDuration(minutes)}` };
  });

  // `<LifecycleTimeline>` renders `ts` verbatim as the milestone's own display line -- format
  // it here, last, so every span/position calc above still ran against the real timestamp.
  return withSpans.map((m) => ({ ...m, ts: m.ts == null ? null : fmtDate(m.ts) }));
}
