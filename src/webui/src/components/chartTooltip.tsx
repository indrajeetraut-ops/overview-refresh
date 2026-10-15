/**
 * The shared chart tooltip (Overview refresh Track C): a small label / value card. Used as a
 * Recharts `content` element, `<Tooltip content={<ChartTooltip describe={...} />} />`; Recharts
 * injects `active`, `payload` and `label`, and `describe` turns them into a title and rows, so
 * every chart words its own facts and none of them styles a tooltip. Exported from `charts.tsx`.
 * It lives in its own file so `ui.tsx` (the donuts) can use it without importing `charts.tsx`,
 * which already imports `ui.tsx`.
 */
import type { ReactNode } from "react";
import { MONO_FONT } from "../present/fonts";

export type TipRow = {
  label: string;
  value: string;
  /** A colour swatch before the label. */
  color?: string;
  /** Colours the value (over budget red, remaining green). */
  tone?: "red" | "green" | "amber";
};
export type TipContent = { title?: ReactNode; rows: TipRow[] };
/** One Recharts payload entry, as far as a tooltip needs it. */
export type TipItem = { name?: unknown; value?: unknown; dataKey?: unknown; color?: string; payload?: any };

const TONE = { red: "var(--red)", green: "var(--green)", amber: "var(--amber)" } as const;

/** The tooltip box itself (also usable on its own). */
export function ChartTooltipBox({ title, rows }: TipContent) {
  return (
    <div
      className="rounded-md px-2.5 py-2 text-xs shadow-lg"
      style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)", fontFamily: MONO_FONT, minWidth: 120 }}
    >
      {title != null && <div className="font-medium mb-1">{title}</div>}
      {rows.map((r, i) => (
        <div key={`${i}-${r.label}`} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5" style={{ color: "var(--muted-foreground)" }}>
            {r.color && <span aria-hidden className="inline-block w-2 h-2 rounded-full" style={{ background: r.color }} />}
            {r.label}
          </span>
          <span style={{ color: r.tone ? TONE[r.tone] : "var(--foreground)" }}>{r.value}</span>
        </div>
      ))}
    </div>
  );
}

/** Recharts `content`: renders nothing unless active and `describe` returns something. */
export function ChartTooltip({ active, payload, label, describe }: {
  active?: boolean;
  payload?: readonly TipItem[];
  label?: unknown;
  describe: (p: { label: unknown; items: readonly TipItem[] }) => TipContent | null;
}) {
  if (!active || !payload?.length) return null;
  const content = describe({ label, items: payload });
  return content ? <ChartTooltipBox {...content} /> : null;
}
