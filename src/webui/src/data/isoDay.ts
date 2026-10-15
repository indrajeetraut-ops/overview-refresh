/**
 * ISO `YYYY-MM-DD` day helpers with no local-time parsing: a day is split as text and day
 * arithmetic goes through `Date.UTC`, so a browser west of UTC never shows the day before.
 * Shared by the fixture generator (`data/mock.ts`) and the presentation layer
 * (`present/forecast.ts`, which re-exports them).
 */

/** Year / month / day of an ISO day, or `null` for something that is not a real date
 * ("2026-13-01", "2026-02-30"). Any time part after the day is ignored. */
export function splitIso(iso: string): { y: number; m: number; d: number } | null {
  const hit = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!hit) return null;
  const y = Number(hit[1]), m = Number(hit[2]), d = Number(hit[3]);
  const t = new Date(Date.UTC(y, m - 1, d));
  if (m < 1 || m > 12 || t.getUTCMonth() !== m - 1 || t.getUTCDate() !== d) return null;
  return { y, m, d };
}

/** ISO day plus `n` days (negative allowed), as ISO. */
export function addDays(iso: string, n: number): string {
  const p = splitIso(iso);
  if (!p) return iso;
  return new Date(Date.UTC(p.y, p.m - 1, p.d + n)).toISOString().slice(0, 10);
}

/** Every ISO day of `start..end` inclusive. */
export function eachDay(start: string, end: string): string[] {
  const days: string[] = [];
  for (let d = start; d <= end && days.length < 800; d = addDays(d, 1)) days.push(d);
  return days;
}
