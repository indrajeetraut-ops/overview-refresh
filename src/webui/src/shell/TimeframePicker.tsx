/**
 * The timeframe picker (Overview refresh A3a): a button that opens a grouped listbox, "Budget
 * periods" (newest first, dates, "· current") then "Other ranges" (rolling 15 / 30 / 60 days).
 * Keyboard: ArrowDown / ArrowUp / Home / End move, Enter or Space picks, Escape closes and
 * returns focus to the button, Tab closes. Focus stays on the listbox, which names the active
 * option through `aria-activedescendant`. Grouping, labels and dates come from
 * `present/timeframe.ts`; this file only lays them out.
 *
 * `FilterBar` hosts this component; its layout contract is in that file's header.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { flatOptions, pickerGroups, pickerNav, PERIODS_UNAVAILABLE_NOTE, selectionForKey } from "../present/timeframe";
import type { BudgetPeriod, TimeframeSelection } from "../present/timeframe";

type Props = {
  selection: TimeframeSelection;
  periods: BudgetPeriod[];
  /** The periods request failed: say so, the rolling ranges still work. */
  unavailable: boolean;
  onSelect: (s: TimeframeSelection) => void;
};

export default function TimeframePicker({ selection, periods, unavailable, onSelect }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);

  const groups = useMemo(() => pickerGroups(periods, unavailable), [periods, unavailable]);
  const options = useMemo(() => flatOptions(groups), [groups]);
  const optionId = (key: string) => `${id}-${key}`;

  function openList() {
    const i = options.findIndex((o) => o.key === selection.key);
    setActive(i >= 0 ? i : 0);
    setOpen(true);
  }
  function close(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) button.current?.focus();
  }
  function choose(key: string) {
    // A rolling range ends "now": it is resolved here, at the click, never during render.
    const next = selectionForKey(key, periods, new Date());
    if (next) onSelect(next);
    close(true);
  }

  useEffect(() => {
    if (open) list.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) close(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => {
    if (open && options[active]) document.getElementById(optionId(options[active].key))?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]); // eslint-disable-line react-hooks/exhaustive-deps

  function onButtonKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      openList();
    }
  }
  function onListKey(e: React.KeyboardEvent) {
    switch (e.key) {
      case "ArrowDown": case "ArrowUp": case "Home": case "End":
        e.preventDefault();
        setActive((i) => pickerNav(i, e.key, options.length - 1));
        break;
      case "Enter":
      case " ": e.preventDefault(); if (options[active]) choose(options[active].key); break;
      case "Escape": e.preventDefault(); e.stopPropagation(); close(true); break;
      case "Tab": close(false); break;
    }
  }

  const listId = `${id}-list`;
  return (
    // Positioned only from `sm` up: below it the listbox anchors to the nearest positioned
    // ancestor, the sticky `FilterBar`, and spans its row (see the listbox's classes).
    <div ref={root} className="sm:relative flex min-w-0 items-center gap-2">
      {unavailable && (
        <span className="min-w-0 truncate text-[11px]" title={PERIODS_UNAVAILABLE_NOTE} style={{ color: "var(--muted-foreground)" }}>{PERIODS_UNAVAILABLE_NOTE}</span>
      )}
      <button
        ref={button}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`Timeframe: ${selection.label}`}
        onClick={() => (open ? close(false) : openList())}
        onKeyDown={onButtonKey}
        className="flex items-center gap-2 rounded-md px-3 py-1 text-xs font-mono"
        style={{ background: "var(--muted)", color: "var(--foreground)", border: "1px solid var(--border)", maxWidth: 220 }}
      >
        <span className="truncate">{selection.label}</span>
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden>
          <path d="M2 3.5 5 6.5 8 3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div
          ref={list}
          id={listId}
          role="listbox"
          tabIndex={-1}
          aria-label="Timeframe"
          aria-activedescendant={options[active] ? optionId(options[active].key) : undefined}
          onKeyDown={onListKey}
          // Below `sm` (the bar may wrap, leaving the button anywhere in the row) the listbox spans
          // the bar's padding box inset by the 16px page gutter, so it is inside the viewport at
          // any width. From `sm` it hangs under the button, right-aligned: the bar may still wrap, but the
          // picker sits in its `ml-auto` right cluster, so 280px of room to its left remains.
          className="absolute left-4 right-4 top-full z-20 overflow-y-auto rounded-md py-1 outline-none sm:left-auto sm:right-0 sm:top-[calc(100%+6px)] sm:w-[280px]"
          style={{
            // never taller than the viewport leaves room for (the top bar and the filter bar sit above it)
            maxHeight: "min(340px, calc(100dvh - 190px))",
            background: "var(--card)", border: "1px solid var(--border)", boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
          }}
        >
          {/* TODO(custom range): custom date range is deferred; it joins the "Other ranges" group */}
          {groups.map((g, gi) => (
            <div key={g.heading} role="group" aria-labelledby={`${id}-g${gi}`} style={gi > 0 ? { borderTop: "1px solid var(--border)", marginTop: 4, paddingTop: 4 } : undefined}>
              <div id={`${id}-g${gi}`} className="px-3 py-1 text-[10px] font-medium uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>
                {g.heading}
              </div>
              {g.emptyNote && (
                <div className="px-3 py-1.5 text-xs" style={{ color: "var(--muted-foreground)" }}>{g.emptyNote}</div>
              )}
              {g.options.map((o) => {
                const idx = options.findIndex((x) => x.key === o.key);
                const selected = o.key === selection.key;
                return (
                  <div
                    key={o.key}
                    id={optionId(o.key)}
                    role="option"
                    aria-selected={selected}
                    // pointer movement only: a list scrolling under a still pointer must not steal the keyboard's option
                    onMouseMove={() => idx !== active && setActive(idx)}
                    onClick={() => choose(o.key)}
                    className="flex cursor-pointer flex-col px-3 py-1.5 text-xs"
                    style={{
                      background: idx === active ? "var(--muted)" : "transparent",
                      color: "var(--foreground)", fontWeight: selected ? 600 : 400,
                    }}
                  >
                    <span>{o.label}</span>
                    {(o.detail || o.current) && (
                      <span className="font-mono text-[11px]" style={{ color: "var(--muted-foreground)", fontWeight: 400 }}>
                        {o.detail}{o.current ? `${o.detail ? " " : ""}· current` : ""}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
