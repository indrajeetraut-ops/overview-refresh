/**
 * Breadcrumb label registry (Overview refresh D2). The navigation stack carries ids only; an
 * entity's name is known to the screen that fetched it. Each entity screen publishes that name
 * here once its own detail has loaded (`useCrumbLabel`), so the top bar can say "Platform"
 * instead of "Team" without a request of its own. Names persist for the session, so crumbs for
 * pages already visited stay named after the viewer goes back.
 *
 * A team / project crumb whose detail has not loaded yet says the `scopes()` name (fetched once at
 * Shell level for managers, `present/crumbs.ts::scopeLabels`), passed to `TopBar` as a fallback.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { labelKey } from "../present/crumbs";

type Ctx = { labels: Readonly<Record<string, string>>; setLabel: (key: string, name: string) => void };

const NOOP: Ctx = { labels: {}, setLabel: () => {} };
const LabelCtx = createContext<Ctx>(NOOP);

export function CrumbLabelsProvider({ children }: { children: React.ReactNode }) {
  const [labels, setLabels] = useState<Record<string, string>>({});
  const setLabel = useCallback((key: string, name: string) => {
    setLabels((prev) => (prev[key] === name ? prev : { ...prev, [key]: name }));
  }, []);
  const value = useMemo(() => ({ labels, setLabel }), [labels, setLabel]);
  return <LabelCtx.Provider value={value}>{children}</LabelCtx.Provider>;
}

export function useCrumbLabels(): Readonly<Record<string, string>> {
  return useContext(LabelCtx).labels;
}

/** Publish an entity's display name for its breadcrumb. Call it with `undefined` until loaded. */
export function useCrumbLabel(kind: string, id: string, name: string | null | undefined): void {
  const { setLabel } = useContext(LabelCtx);
  useEffect(() => {
    if (name) setLabel(labelKey(kind, id), name);
  }, [kind, id, name, setLabel]);
}
