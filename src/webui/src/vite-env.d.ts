/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Data seam selector (U1): "mock" (default) or "api". */
  readonly VITE_DATA_SOURCE?: "mock" | "api";
  /** Viewer role for local work (U4/F2), until /v1/me lands: "dev" | "lead" | "admin". */
  readonly VITE_VIEWER_ROLE?: "dev" | "lead" | "admin";
  /** The trusted-header viewer id (`x-talium-viewer`, F2's `TRUSTED_VIEWER_HEADER`) `ApiSource`
   * sends with every request in `api` mode -- a production deployment's auth proxy sets this
   * upstream of the API; for local dev against a live backend, set it here to whatever
   * directory identity you want to view as. Unset sends no header at all (every manager
   * route then 401s, same as an unauthenticated caller; single-user routes are unaffected --
   * F2's own posture, unauthenticated by design). */
  readonly VITE_VIEWER_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
