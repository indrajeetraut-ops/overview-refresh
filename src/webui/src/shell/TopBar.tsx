/**
 * The top bar (Overview refresh D2): on every screen, sticky above the filter bar. Left to right:
 * the mobile hamburger + compact wordmark (below `md`, where the sidebar is a drawer), the
 * breadcrumb, then Configure (lead/admin) and Ask AI (inert until Track K).
 *
 * The breadcrumb is the navigation stack (`present/crumbs.ts`); a crumb click navigates through
 * `onCrumb`. Below `md` only the current (last) crumb shows. The screens' own back buttons stay.
 */
import { useEffect, useRef, useState } from "react";

import { buildCrumbs, collapseCrumbs, isEllipsis, mergeLabels } from "../present/crumbs";
import type { CrumbTarget, StackEntry } from "../present/crumbs";
import { useCrumbLabels } from "./crumbs";

/** Height of the bar; the filter bar sticks right under it (`top-12`). */
export const TOP_BAR_HEIGHT_CLASS = "h-12";

/** `scroll-padding-top` (px) for the scrolling pane under the two sticky bars: the top bar (48px)
 * plus the filter bar, which wraps to two rows (about 85px) on a phone. A focused control scrolls
 * clear of both. */
export const STICKY_BARS_SCROLL_PADDING = 136;

type Props = {
  stack: readonly StackEntry[];
  /** Names to use until a screen has published its own (team / project names from `scopes()`). */
  fallbackLabels?: Readonly<Record<string, string>>;
  onCrumb: (target: CrumbTarget) => void;
  onOpenNav: () => void;
  /** Lead/admin only (App.tsx's role gate); omitted for a dev viewer. */
  onConfigure?: () => void;
  /** True while the Configure screen is the current page: the button is then a no-op, shown as active. */
  configureActive?: boolean;
};

const btn = "flex shrink-0 items-center gap-1.5 rounded px-2 py-1.5 text-xs font-medium";
const btnStyle = { border: "1px solid var(--border)", background: "var(--card)", color: "var(--muted-foreground)" } as const;

export default function TopBar({ stack, fallbackLabels, onCrumb, onOpenNav, onConfigure, configureActive }: Props) {
  const labels = useCrumbLabels();
  const crumbs = buildCrumbs(stack, mergeLabels(fallbackLabels, labels));
  const [expanded, setExpanded] = useState(false);
  const list = useRef<HTMLOListElement>(null);
  const focusRevealed = useRef(false);
  // a different trail (not just a different length) is a new page: fold it again
  const trailKey = stack.map((e) => (typeof e === "string" ? e : `${e.type}:${e.id}`)).join("|");
  useEffect(() => setExpanded(false), [trailKey]);
  // The "…" button unmounts when it expands the trail: hand focus to the first crumb it revealed.
  useEffect(() => {
    if (expanded && focusRevealed.current) {
      focusRevealed.current = false;
      list.current?.querySelector<HTMLElement>(`[data-crumb="${crumbs[1]?.key}"]`)?.focus();
    }
  }, [expanded, crumbs]);
  const items = collapseCrumbs(crumbs, expanded);

  return (
    <header
      className={`sticky top-0 z-30 ${TOP_BAR_HEIGHT_CLASS} px-4 sm:px-9 md:px-12`}
      style={{ background: "var(--background)", borderBottom: "1px solid var(--border)" }}
    >
      <div className="mx-auto flex h-full w-full max-w-[1440px] items-center gap-3">
        <button
          type="button"
          onClick={onOpenNav}
          aria-label="Open navigation"
          className="shrink-0 rounded p-1.5 md:hidden"
          style={{ border: "1px solid var(--border)", background: "var(--card)" }}
        >
          <svg width="16" height="16" viewBox="0 0 14 14" fill="none" aria-hidden>
            <path d="M1.5 3.5h11M1.5 7h11M1.5 10.5h11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
        </button>
        <span className="font-serif shrink-0 md:hidden" style={{ fontSize: 16, color: "var(--foreground)" }}>Talium</span>

        <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
          <ol ref={list} role="list" className="flex min-w-0 items-center gap-1.5 text-xs">
            {items.map((it, i) => {
              const last = i === items.length - 1;
              // below md only the current crumb is shown
              const li = `${last ? "flex" : "hidden md:flex"} min-w-0 items-center gap-1.5`;
              const sep = i > 0 ? (
                <span aria-hidden className="hidden shrink-0 md:inline" style={{ color: "var(--muted-foreground)" }}>›</span>
              ) : null;
              if (isEllipsis(it)) {
                return (
                  <li key={it.key} className={li}>
                    {sep}
                    <button
                      type="button"
                      onClick={() => { focusRevealed.current = true; setExpanded(true); }}
                      aria-label={`Show ${it.hidden} more steps in the path`}
                      title="Show the full path"
                      className="shrink-0 rounded px-1"
                      style={{ color: "var(--muted-foreground)" }}
                    >…</button>
                  </li>
                );
              }
              return (
                <li key={it.key} className={li}>
                  {sep}
                  {it.current ? (
                    <span aria-current="page" title={it.label} className="max-w-[16rem] truncate text-sm font-medium" style={{ color: "var(--foreground)" }}>
                      {it.label}
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onCrumb(it.target)}
                      data-crumb={it.key}
                      title={it.label}
                      className="max-w-[12rem] truncate rounded hover:underline"
                      style={{ color: "var(--muted-foreground)" }}
                    >
                      {it.label}
                    </button>
                  )}
                </li>
              );
            })}
          </ol>
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          {onConfigure && (
            <button
              type="button"
              onClick={configureActive ? undefined : onConfigure}
              aria-label="Configure budgets"
              title="Configure budgets"
              className={btn}
              style={configureActive ? { ...btnStyle, background: "var(--muted)", color: "var(--foreground)" } : btnStyle}
            >
              {/* sliders glyph: reads as settings, and not as the sidebar's sun/moon toggle */}
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
                <path d="M1.5 3.5h11M1.5 7h11M1.5 10.5h11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                <circle cx="4.5" cy="3.5" r="1.5" fill="var(--card)" stroke="currentColor" strokeWidth="1.3" />
                <circle cx="9.5" cy="7" r="1.5" fill="var(--card)" stroke="currentColor" strokeWidth="1.3" />
                <circle cx="5.5" cy="10.5" r="1.5" fill="var(--card)" stroke="currentColor" strokeWidth="1.3" />
              </svg>
              <span className="hidden sm:inline">Configure</span>
            </button>
          )}
          {/* TODO(Track K): wire Ask AI */}
          <button
            type="button"
            disabled
            aria-label="Ask AI (coming soon)"
            title="Coming soon"
            className={`${btn} cursor-not-allowed opacity-60`}
            style={btnStyle}
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
              <path d="M7 1.5l1.2 3.3 3.3 1.2-3.3 1.2L7 10.5 5.8 7.2 2.5 6l3.3-1.2L7 1.5z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
            </svg>
            <span className="hidden sm:inline">Ask AI</span>
          </button>
        </div>
      </div>
    </header>
  );
}
