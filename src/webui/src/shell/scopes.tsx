/**
 * The org's scopes (teams and projects, names only), fetched ONCE at Shell level for managers and
 * shared: the filter bar's team select, the breadcrumb's team / project fallback names, and the
 * scope pickers on Economics and Configure all read it, so none of them requests `GET /v1/scopes`
 * again. It is timeframe-independent and names only, so there is nothing to refresh.
 *
 * A `dev` viewer has no team views, so nothing is fetched and `data` stays `null` (the screens that
 * read it are manager-only). While loading, or after a failed request, `status` says so and the
 * readers show no options, as they did when each fetched its own copy.
 */
import React, { createContext, useContext } from "react";

import { useData } from "../data/source";
import type { DataState, Schemas } from "../data/source";
import { canSeeManagerNav, useViewer } from "./viewer";

export type ScopesState = DataState<Schemas["ScopesResponse"] | null>;

const Ctx = createContext<ScopesState>({ status: "loading", data: null, error: null });

export function ScopesProvider({ children }: { children: React.ReactNode }) {
  const manager = canSeeManagerNav(useViewer().role);
  const state = useData((src) => (manager ? src.scopes() : Promise.resolve(null)), [manager]);
  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export function useScopes(): ScopesState {
  return useContext(Ctx);
}
