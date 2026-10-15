/**
 * The five-state block wrapper (MVP plan U3 / BLUE §6). Every widget renders through this so
 * it ships all five states and picks between them **from the envelope**, never by guessing:
 *
 *   loading      skeleton that reserves the final height (no layout jump)
 *   empty        the subsystem ran, nothing in range
 *   cold-start   the subsystem is not computing yet, but will (resolves over time)
 *   unavailable  the mode state — the envelope's {unavailable:{block,requires}}; visually
 *                distinct; carries the SourceNote + enrolment path (MODES §5)
 *   error        inline, with retry
 *
 * cold-start ≠ unavailable is the load-bearing distinction — never substitute one for the other.
 */
import React from "react";

import type { DataState } from "../data/source";
import type { Envelope, UnavailableBlock } from "../data/source";

export type BlockState<T> =
  | { kind: "loading" }
  | { kind: "error"; error: Error }
  | { kind: "unavailable"; info: UnavailableBlock }
  | { kind: "cold-start" }
  | { kind: "empty" }
  | { kind: "ready"; data: T };

export type ResolveOpts<T> = {
  /** True when `data` is structurally present but semantically empty (in-range, no rows). */
  isEmpty?: (data: T) => boolean;
  /** True when a present payload still has a section that has not computed yet (rare). */
  isColdStart?: (data: T) => boolean;
};

/** Pure: map a `useData` result over an envelope to one of the five states. Unit-tested. */
export function resolveBlockState<T>(
  state: DataState<Envelope<T>>,
  opts: ResolveOpts<T> = {},
): BlockState<T> {
  if (state.status === "loading") return { kind: "loading" };
  if (state.status === "error") return { kind: "error", error: state.error };

  const env = state.data;
  if (env.unavailable) return { kind: "unavailable", info: env.unavailable };
  if (env.data == null) return { kind: "cold-start" };
  if (opts.isColdStart?.(env.data)) return { kind: "cold-start" };
  if (opts.isEmpty?.(env.data)) return { kind: "empty" };
  return { kind: "ready", data: env.data };
}

// ── presentational pieces ───────────────────────────────────────────────────────
export function Skeleton({ minHeight = 96 }: { minHeight?: number }) {
  return (
    <div
      className="rounded-lg animate-pulse"
      style={{ minHeight, background: "var(--muted)", opacity: 0.6 }}
      aria-hidden
    />
  );
}

export function Frame({ minHeight, tone, children }: {
  minHeight: number; tone: "muted" | "amber" | "red"; children: React.ReactNode;
}) {
  const border = tone === "amber" ? "var(--amber)" : tone === "red" ? "var(--red)" : "var(--border)";
  const bg = tone === "amber" ? "var(--amber-bg)" : tone === "red" ? "var(--red-bg)" : "var(--card)";
  return (
    <div
      className="rounded-lg flex flex-col items-start justify-center gap-1.5 px-5 py-4 text-sm"
      style={{ minHeight, border: `1px solid ${border}`, background: bg, color: "var(--muted-foreground)" }}
      role={tone === "red" ? "alert" : undefined}
    >
      {children}
    </div>
  );
}

/** MODES §5: name the source, not the user; offer the enrolment path. */
export function SourceNote({ info }: { info: UnavailableBlock }) {
  return (
    <span style={{ fontSize: 12 }}>
      Needs <strong style={{ color: "var(--foreground)" }}>{info.requires}</strong> data — not
      connected for this view.
    </span>
  );
}

/** A permanently-unavailable section with no fetch of its own -- a screen area whose backend
 * capability genuinely doesn't exist yet in this phase (e.g. Overview's forecast section,
 * gated on X5/Phase 3). Same visual as `<Block>`'s own unavailable state. */
export function UnavailableNote({ info, minHeight = 96 }: { info: UnavailableBlock; minHeight?: number }) {
  return (
    <Frame minHeight={minHeight} tone="amber">
      <SourceNote info={info} />
    </Frame>
  );
}

// ── the wrapper ────────────────────────────────────────────────────────────────
export type BlockProps<T> = {
  state: DataState<Envelope<T>>;
  /** Height the loading skeleton reserves — set to the widget's real height to avoid jump. */
  minHeight?: number;
  emptyLabel?: string;
  coldStartLabel?: string;
  onRetry?: () => void;
  children: (data: T) => React.ReactNode;
} & ResolveOpts<T>;

export function Block<T>({
  state, minHeight = 96, emptyLabel = "Nothing in this range.",
  coldStartLabel = "Still gathering data for this — check back shortly.",
  onRetry, isEmpty, isColdStart, children,
}: BlockProps<T>) {
  const s = resolveBlockState(state, { isEmpty, isColdStart });
  switch (s.kind) {
    case "loading":
      return <Skeleton minHeight={minHeight} />;
    case "error":
      return (
        <Frame minHeight={minHeight} tone="red">
          <span style={{ color: "var(--red)" }}>Couldn't load this.</span>
          {onRetry && (
            <button onClick={onRetry} className="text-xs font-medium underline" style={{ color: "var(--foreground)" }}>
              Retry
            </button>
          )}
        </Frame>
      );
    case "unavailable":
      return (
        <Frame minHeight={minHeight} tone="amber">
          <SourceNote info={s.info} />
        </Frame>
      );
    case "cold-start":
      return <Frame minHeight={minHeight} tone="muted">{coldStartLabel}</Frame>;
    case "empty":
      return <Frame minHeight={minHeight} tone="muted">{emptyLabel}</Frame>;
    case "ready":
      return <>{children(s.data)}</>;
  }
}
