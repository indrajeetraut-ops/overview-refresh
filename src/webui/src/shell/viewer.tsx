/**
 * Viewer identity + role (MVP plan U4 / F2). Manager surfaces are role-gated: a `dev` sees
 * only "My usage"; `lead`/`admin` see the manager nav. AuthN is a later SSO seam — in MVP
 * the viewer comes from a trusted header/config on the backend; the front-end reads it from
 * `VITE_VIEWER_ROLE` for now (default `admin` for local work) and will fetch it from the API
 * once F2 exposes `/v1/me`.
 */
import React, { createContext, useContext, useMemo } from "react";

export type Role = "dev" | "lead" | "admin";

export type Viewer = {
  userId: string;
  role: Role;
  /** Team ids this viewer may see (lead scope); empty for admin = all. */
  teams: string[];
};

const DEFAULT: Viewer = {
  userId: "me",
  role: (import.meta.env.VITE_VIEWER_ROLE as Role) ?? "admin",
  teams: [],
};

const Ctx = createContext<Viewer>(DEFAULT);

export function ViewerProvider({ children, viewer = DEFAULT }: { children: React.ReactNode; viewer?: Viewer }) {
  const value = useMemo(() => viewer, [viewer]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useViewer(): Viewer {
  return useContext(Ctx);
}

/** True when the viewer may see cross-user manager surfaces (Overview/Usage/Economics/Teams/Projects). */
export function canSeeManagerNav(role: Role): boolean {
  return role !== "dev";
}
