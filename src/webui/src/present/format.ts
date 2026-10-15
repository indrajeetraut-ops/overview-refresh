/**
 * Presentation-layer formatters (MVP plan U2 — the §1 consequence). The API returns
 * structured/numeric values; the client owns every display string. Money/token/percent
 * output is meant to render in a `tabular-nums` context (see `num` class in index.css).
 */

/** Minutes → "3h 20m" / "45m" / "0m". Source field: `active_minutes` and friends. */
export function fmtDuration(minutes: number | null | undefined): string {
  if (minutes == null || !isFinite(minutes)) return "—";
  const m = Math.max(0, Math.round(minutes));
  const h = Math.floor(m / 60);
  const rem = m % 60;
  if (h === 0) return `${rem}m`;
  if (rem === 0) return `${h}h`;
  return `${h}h ${rem}m`;
}

/** Days (may be fractional) → "2.4d" / "5d". Source: a number + unit, never `"2.4d"` on the wire. */
export function fmtDays(days: number | null | undefined): string {
  if (days == null || !isFinite(days)) return "—";
  return (Number.isInteger(days) ? String(days) : days.toFixed(1)) + "d";
}

/** Days (may be tiny) → the most readable unit: "<1m" / "18m" / "7.9h" / "3.3d". For spans like
 * time-to-merge where most values are well under a day and `fmtDays` would print "0.0d". */
export function fmtSpanDays(days: number | null | undefined): string {
  if (days == null || !isFinite(days)) return "—";
  const minutes = days * 24 * 60;
  if (minutes < 1) return "<1m";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const hours = minutes / 60;
  if (hours < 24) return `${hours < 10 ? hours.toFixed(1).replace(/\.0$/, "") : Math.round(hours)}h`;
  return fmtDays(days < 10 ? Math.round(days * 10) / 10 : Math.round(days));
}

const REL_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31_536_000], ["month", 2_592_000], ["week", 604_800],
  ["day", 86_400], ["hour", 3_600], ["minute", 60], ["second", 1],
];
const REL = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "narrow" });

/** ISO timestamp → "2h ago" / "3d ago" / "just now". Source: `last_active_at` etc. as ISO. */
export function fmtRelativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.round((then - now.getTime()) / 1000);
  const abs = Math.abs(secs);
  if (abs < 45) return "just now";
  for (const [unit, span] of REL_UNITS) {
    if (abs >= span || unit === "second") return REL.format(Math.round(secs / span), unit);
  }
  return "just now";
}

/** ISO timestamp → "Sep 3" / "Sep 3, 2026" (when not the current year). */
export function fmtDate(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString("en-US", sameYear
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" });
}

/** Whole-dollar string from a micro-USD integer (`Money.usd_micros`). */
export function fmtMoneyMicros(usdMicros: number | null | undefined): string {
  if (usdMicros == null) return "—";
  return fmtUSD(usdMicros / 1_000_000);
}

/** Whole-dollar string from a dollar number. */
export function fmtUSD(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  return "$" + Math.round(n).toLocaleString("en-US");
}

/** Token count → "2.4M" / "320K" / "812". */
export function fmtTokens(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return Math.round(n / 1_000) + "K";
  return String(Math.round(n));
}

/** A ratio in [0,1] (or a value/total pair) → "72%". */
export function fmtPct(value: number, total = 1): string {
  if (!isFinite(value) || !isFinite(total) || total === 0) return "—";
  return Math.round((value / total) * 100) + "%";
}

/** Integer count with thousands separators. */
export function fmtCount(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  return Math.round(n).toLocaleString("en-US");
}
