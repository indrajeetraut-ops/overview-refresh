/**
 * The data seam (MVP plan Track U1/U5). Every screen gets its data through a `DataSource` +
 * the `useData` hook — never a direct `mock.ts` import. `VITE_DATA_SOURCE` selects the impl:
 *
 *   mock (default) — in-repo fixtures, no backend needed
 *   api            — fetch /v1/* from the FastAPI dashboard API (dev: proxied to :8090)
 *
 * **Contract.** The wire shapes are backend-owned: Pydantic models in `server/api/schemas/`
 * → `openapi.json` → `api-types.ts` (generated, `utils/gen_api_types.sh`). Every Track A
 * method below is typed against the generated `Envelope_*_` schemas — the real contract, not
 * a placeholder. The single-user surface (`/v1/users/{id}/overview` etc.) is still a bare
 * dict on the wire (F0 ports each to a model as its screen is wired; MyUsage's U5 cutover
 * only needed `myTips`, so `overview`/`rhythm`/`sessions` stay untyped here for now).
 */
import { useEffect, useState } from "react";

import type { components } from "./api-types";

export type Schemas = components["schemas"];

// ── envelope (re-exported from the generated schema, not hand-duplicated) ────────
export type Money = Schemas["Money"];
export type EntityRef = Schemas["EntityRef"];
export type Finding = Schemas["Finding"];
export type UnavailableBlock = Schemas["UnavailableBlock"];
export type SessionMode = Schemas["SessionMode"];
export type AggregateMode = Schemas["AggregateMode"];

/** The shape every `Envelope_*_` generated type shares — generic over its `data`. */
export type Envelope<T> = {
  data?: T | null;
  mode: SessionMode | AggregateMode;
  unavailable?: UnavailableBlock | null;
};

// ── request params ──────────────────────────────────────────────────────────────
/** A resolved half-open window `[from, to)` (UTC ISO): the selected budget period or rolling
 * range, resolved once by `shell/timeframe.tsx`. `ApiSource` turns it into `from`/`to` query
 * params. (The API still accepts a named `period=`; the app no longer sends it.) */
export type Timeframe = { from: string; to: string };
export type Scope = "org" | (string & {}); // "org" | <team id> | <project id>

export function periodParams(tf: Timeframe): Record<string, string> {
  return { from: tf.from, to: tf.to };
}

export type TableParams = { sort?: string; q?: string; limit?: number; offset?: number };

function tableParams(tf: Timeframe, extra: TableParams): Record<string, string> {
  const params = periodParams(tf);
  if (extra.sort) params.sort = extra.sort;
  if (extra.q) params.q = extra.q;
  if (extra.limit != null) params.limit = String(extra.limit);
  if (extra.offset != null) params.offset = String(extra.offset);
  return params;
}

/** E1's two-part scope (`scope_type`/`scope_id`), the same shape F1's budgets routes use --
 * never a single composite `scope=` string. */
export type EconomicsScope = { scopeType: "org" | "team" | "repo"; scopeId: string };

// The single-tenant org's scope id: its one budget row, and its forecast scope, live at
// `scope_id: config.directory.default_org_id` (`config/directory.yaml`'s `default_org_id:
// default`) -- `org_overview()` reads the budget with exactly that id, so writing anything else
// would silently never show up on Overview's budget tile, and the forecast run maps its org
// scope to the same id. Not derived from an endpoint: no `/v1/me`-style route surfaces it yet.
// Limitation: an org whose id is not "default" gets `forecast: null` here and the Economics
// screen falls back to its flat-rate chart.
export const ORG_SCOPE_ID = "default";

/** The scope the forecast endpoints take: `/v1/economics` ignores the org's `scope_id` (the
 * picker sends the literal `"org"`), but a forecast run is keyed by the real org id. Teams and
 * repos pass through unchanged (a repo id such as `github:acme/auth-service` as it is). */
export function forecastScope(scope: EconomicsScope): EconomicsScope {
  return scope.scopeType === "org" ? { scopeType: "org", scopeId: ORG_SCOPE_ID } : scope;
}

/** `GET /v1/users/{id}/repos` -- one `single_user/service.py::_repo_rollup` row (dashboard
 * plan §3), by hand rather than generated (that route isn't F0-typed yet). */
export type RepoRollupRow = {
  repo_id: string; kind: string; label: string; sessions: number;
  cost_usd_micros: number; tokens_total: number;
  files_created: number; files_edited: number; lines_added: number; lines_removed: number;
};

/** `GET /v1/users/{id}/sessions` -- one `single_user/service.py::_session_row_view` row, by
 * hand for the same reason as `RepoRollupRow`. */
export type SessionRow = {
  session_id: string; datasource: string | null; vendor: string | null;
  started_at: string | null; duration_s: number | null;
  tokens_total: number | null; cost_usd_micros: number | null;
  title: string | null; repo: string | null; badges: string[];
};

// ── the seam ────────────────────────────────────────────────────────────────────
export interface DataSource {
  orgOverview(tf: Timeframe): Promise<Schemas["Envelope_OrgOverview_"]>;
  orgUsage(tf: Timeframe): Promise<Schemas["Envelope_OrgUsage_"]>;
  teams(tf: Timeframe, params?: TableParams): Promise<Schemas["Envelope_TeamsPage_"]>;
  projects(tf: Timeframe, params?: TableParams): Promise<Schemas["Envelope_ProjectsPage_"]>;
  team(id: string, tf: Timeframe): Promise<Schemas["Envelope_TeamDetail_"]>;
  person(id: string, tf: Timeframe): Promise<Schemas["Envelope_PersonDetail_"]>;
  project(id: string, tf: Timeframe): Promise<Schemas["Envelope_ProjectDetail_"]>;
  myTips(userId: string, tf: Timeframe): Promise<Schemas["Envelope_MyTips_"]>;
  prDetail(id: string): Promise<Schemas["Envelope_PRDetail_"]>;
  featureDetail(id: string): Promise<Schemas["Envelope_FeatureDetail_"]>;
  economics(scope: EconomicsScope, tf: Timeframe): Promise<Schemas["Envelope_Economics_"]>;
  /** Forecast T7: `GET /v1/forecast` -- the precomputed forecast of one scope with every preset.
   * Pass the scope through `forecastScope` first. `data.forecast` is `null` when no run is
   * loaded or the scope is not in it. */
  forecast(scope: EconomicsScope): Promise<Schemas["Envelope_BudgetForecast_"]>;
  /** Forecast T7: `GET /v1/forecast/plan` -- what every team and project needs per period and
   * preset (org-wide, manager-gated). `data` is `null` + `unavailable` when no run is loaded. */
  forecastPlan(): Promise<Schemas["Envelope_BudgetPlan_"]>;
  /** U7-econ-picker: the scope picker's names-only, timeframe-independent enumeration of
   * every team/project the viewer can see — not envelope-wrapped, same as `pricingModels`. */
  scopes(): Promise<Schemas["ScopesResponse"]>;
  /** F0 reference port — fully typed today. */
  pricingModels(): Promise<Schemas["PricingModelsResponse"]>;
  /** U5-myusage: `GET /v1/users/{id}/overview`, F0-typed (`schemas.single_user.Overview`). */
  myOverview(userId: string, tf: Timeframe): Promise<Schemas["Overview"]>;
  /** U5-myusage: `GET /v1/users/{id}/repos` -- not yet F0-typed (`schemas/README.md`'s own
   * porting table), so `repos` is `RepoRollupRow[] | UnavailableBlock`-shaped by hand rather
   * than generated. */
  myRepos(userId: string, tf: Timeframe): Promise<{ repos: RepoRollupRow[] | { unavailable: UnavailableBlock } }>;
  /** U5-myusage: `GET /v1/users/{id}/sessions` -- not yet F0-typed, same reasoning as
   * `myRepos`. `SessionRow`'s shape is `single_user/service.py::_session_row_view`'s, by hand
   * for the same reason `SessionScreen.tsx`'s `SessionEconomics` is. */
  mySessions(userId: string, tf: Timeframe, limit?: number): Promise<{ sessions: SessionRow[]; has_more: boolean }>;
  sessionDetail(id: string, view?: "economics"): Promise<Record<string, unknown>>;
  /** TZ track: this user's stored `users.timezone` (an IANA zone, or `null` when never set) --
   * not F0-typed, same reasoning as `myOverview`/`sessionDetail` above (this whole surface is
   * a bare dict on the wire until it's ported). */
  getTimezone(userId: string): Promise<{ user_id: string; timezone: string | null }>;
  /** Persists an IANA zone the browser detected via `Intl.DateTimeFormat`. */
  setTimezone(userId: string, timezone: string): Promise<{ user_id: string; timezone: string | null }>;
  /** F1: `GET /v1/budgets/{scope_type}/{scope_id}` -- not envelope-wrapped, same as `scopes()`
   * (`BudgetResponse`'s own precedent). `amount_usd` is `null` when no budget is set yet for
   * this scope. */
  getBudget(scopeType: string, scopeId: string): Promise<Schemas["BudgetResponse"]>;
  /** F1: `PUT /v1/budgets/{scope_type}/{scope_id}`, lead/admin gated on the backend
   * (`gate.require_manager`/`require_team`/`require_repo`). */
  putBudget(scopeType: string, scopeId: string, body: Schemas["BudgetWriteRequest"]): Promise<Schemas["BudgetResponse"]>;
  /** Overview refresh A2: `GET /v1/budget-cycle` -- the org's budget cadence (lead/admin only on
   * the backend; not envelope-wrapped). */
  budgetCycle(): Promise<Schemas["BudgetCycleResponse"]>;
  /** `GET /v1/budget-cycle/periods?history=` -- the periods the timeframe picker offers (exact UTC
   * `from`/`to`, the server being the only place calendar arithmetic happens). Readable by any
   * viewer. */
  budgetPeriods(history?: number): Promise<Schemas["BudgetPeriodsResponse"]>;
  /** `PUT /v1/budget-cycle` -- replaces the cycle (lead/admin). A 400/422 rejects with the
   * server's own message (see `errorMessage`). */
  putBudgetCycle(body: Schemas["BudgetCycleWriteRequest"]): Promise<Schemas["BudgetCyclePutResponse"]>;
}

/** Viewer id — a trusted-header/config seam in the API (F2); hard-coded until auth lands. */
export const MY_USER_ID = "me";

// ── api impl ────────────────────────────────────────────────────────────────────
/**
 * The message for a failed request. The API's own words win: a rule violation is a 400 with
 * `{"reason": "..."}` (`ApiError`), a malformed body a 422 with FastAPI's
 * `{"detail": [{"loc": [...], "msg": "..."}]}` (or a plain string `detail`). Anything else falls
 * back to the status line, as before. Pure, so it is unit-tested.
 */
export function errorMessage(status: number, statusText: string, path: string, bodyText: string): string {
  try {
    const body = JSON.parse(bodyText) as { reason?: unknown; detail?: unknown };
    if (typeof body.reason === "string" && body.reason) return body.reason;
    if (typeof body.detail === "string" && body.detail) return body.detail;
    if (Array.isArray(body.detail)) {
      const parts = body.detail.map((d: { loc?: unknown[]; msg?: unknown }) => {
        if (typeof d?.msg !== "string") return "";
        const field = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
        return field ? `${field}: ${d.msg}` : d.msg;
      }).filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
  } catch {
    // not JSON: use the status line
  }
  return `${status} ${statusText} for ${path}`;
}

async function getJSON<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const qs = new URLSearchParams(params).toString();
  const headers: Record<string, string> = { accept: "application/json" };
  // F2's trusted-header viewer -- a real deployment's auth proxy sets this upstream; for
  // local dev against a live backend, VITE_VIEWER_ID stands in until /v1/me lands (see its
  // own doc comment in vite-env.d.ts). Unset sends no header, same as today.
  if (import.meta.env.VITE_VIEWER_ID) headers["x-talium-viewer"] = import.meta.env.VITE_VIEWER_ID;
  const res = await fetch(qs ? `${path}?${qs}` : path, { headers });
  if (!res.ok) throw new Error(errorMessage(res.status, res.statusText, path, await res.text().catch(() => "")));
  return (await res.json()) as T;
}

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json", "content-type": "application/json" };
  if (import.meta.env.VITE_VIEWER_ID) headers["x-talium-viewer"] = import.meta.env.VITE_VIEWER_ID;
  const res = await fetch(path, { method: "PUT", headers, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(errorMessage(res.status, res.statusText, path, await res.text().catch(() => "")));
  return (await res.json()) as T;
}

export class ApiSource implements DataSource {
  orgOverview(tf: Timeframe) { return getJSON<Schemas["Envelope_OrgOverview_"]>("/v1/org/overview", periodParams(tf)); }
  orgUsage(tf: Timeframe) { return getJSON<Schemas["Envelope_OrgUsage_"]>("/v1/org/usage", periodParams(tf)); }
  teams(tf: Timeframe, params: TableParams = {}) { return getJSON<Schemas["Envelope_TeamsPage_"]>("/v1/teams", tableParams(tf, params)); }
  projects(tf: Timeframe, params: TableParams = {}) { return getJSON<Schemas["Envelope_ProjectsPage_"]>("/v1/projects", tableParams(tf, params)); }
  team(id: string, tf: Timeframe) { return getJSON<Schemas["Envelope_TeamDetail_"]>(`/v1/teams/${encodeURIComponent(id)}`, periodParams(tf)); }
  person(id: string, tf: Timeframe) { return getJSON<Schemas["Envelope_PersonDetail_"]>(`/v1/people/${encodeURIComponent(id)}`, periodParams(tf)); }
  project(id: string, tf: Timeframe) { return getJSON<Schemas["Envelope_ProjectDetail_"]>(`/v1/projects/${id}`, periodParams(tf)); }
  myTips(userId: string, tf: Timeframe) { return getJSON<Schemas["Envelope_MyTips_"]>(`/v1/users/${userId}/tips`, periodParams(tf)); }
  prDetail(id: string) { return getJSON<Schemas["Envelope_PRDetail_"]>(`/v1/prs/${id}`); }
  featureDetail(id: string) { return getJSON<Schemas["Envelope_FeatureDetail_"]>(`/v1/features/${id}`); }
  economics(scope: EconomicsScope, tf: Timeframe) {
    return getJSON<Schemas["Envelope_Economics_"]>(
      "/v1/economics", { scope_type: scope.scopeType, scope_id: scope.scopeId, ...periodParams(tf) },
    );
  }
  forecast(scope: EconomicsScope) {
    return getJSON<Schemas["Envelope_BudgetForecast_"]>(
      "/v1/forecast", { scope_type: scope.scopeType, scope_id: scope.scopeId },
    );
  }
  forecastPlan() { return getJSON<Schemas["Envelope_BudgetPlan_"]>("/v1/forecast/plan"); }
  scopes() { return getJSON<Schemas["ScopesResponse"]>("/v1/scopes"); }
  pricingModels() { return getJSON<Schemas["PricingModelsResponse"]>("/v1/pricing/models"); }
  myOverview(userId: string, tf: Timeframe) { return getJSON<Schemas["Overview"]>(`/v1/users/${userId}/overview`, periodParams(tf)); }
  myRepos(userId: string, tf: Timeframe) {
    return getJSON<{ repos: RepoRollupRow[] | { unavailable: UnavailableBlock } }>(`/v1/users/${userId}/repos`, periodParams(tf));
  }
  mySessions(userId: string, tf: Timeframe, limit = 6) {
    return getJSON<{ sessions: SessionRow[]; has_more: boolean }>(
      `/v1/users/${userId}/sessions`, { ...periodParams(tf), sort: "recent", limit: String(limit) },
    );
  }
  sessionDetail(id: string, view?: "economics") {
    return getJSON<Record<string, unknown>>(`/v1/sessions/${id}`, view ? { view } : {});
  }
  getTimezone(userId: string) {
    return getJSON<{ user_id: string; timezone: string | null }>(`/v1/users/${userId}/timezone`);
  }
  setTimezone(userId: string, timezone: string) {
    return putJSON<{ user_id: string; timezone: string | null }>(`/v1/users/${userId}/timezone`, { timezone });
  }
  // scope_id is not encodeURIComponent'd -- a repo scope_id can carry a literal "/" (e.g.
  // "github:owner/name"), which the backend's `{scope_id:path}` route expects unescaped, the
  // same convention `project(id)` above already follows.
  getBudget(scopeType: string, scopeId: string) {
    return getJSON<Schemas["BudgetResponse"]>(`/v1/budgets/${scopeType}/${scopeId}`);
  }
  putBudget(scopeType: string, scopeId: string, body: Schemas["BudgetWriteRequest"]) {
    return putJSON<Schemas["BudgetResponse"]>(`/v1/budgets/${scopeType}/${scopeId}`, body);
  }
  budgetCycle() { return getJSON<Schemas["BudgetCycleResponse"]>("/v1/budget-cycle"); }
  budgetPeriods(history?: number) {
    return getJSON<Schemas["BudgetPeriodsResponse"]>("/v1/budget-cycle/periods", history != null ? { history: String(history) } : {});
  }
  putBudgetCycle(body: Schemas["BudgetCycleWriteRequest"]) {
    return putJSON<Schemas["BudgetCyclePutResponse"]>("/v1/budget-cycle", body);
  }
}

// ── source selection ───────────────────────────────────────────────────────────
let _override: DataSource | null = null;
/** Tests/Storybook: pin a source regardless of env. Pass `null` to clear. */
export function setDataSource(src: DataSource | null): void {
  _override = src;
}

let _cached: DataSource | null = null;
export function getDataSource(): DataSource {
  if (_override) return _override;
  if (_cached) return _cached;
  _cached = new ApiSource();
  return _cached;
}

// ── the hook ───────────────────────────────────────────────────────────────────
export type DataState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "ready"; data: T; error: null }
  | { status: "error"; data: null; error: Error };

/**
 * Fetch once (and refetch when `deps` change) through the active `DataSource`.
 * `select` receives the source and returns the promise for this screen's payload.
 */
export function useData<T>(
  select: (src: DataSource) => Promise<T>,
  deps: readonly unknown[] = [],
): DataState<T> {
  const [state, setState] = useState<DataState<T>>({ status: "loading", data: null, error: null });
  useEffect(() => {
    let live = true;
    setState({ status: "loading", data: null, error: null });
    select(getDataSource())
      .then((data) => live && setState({ status: "ready", data, error: null }))
      .catch((error) => live && setState({ status: "error", data: null, error: error as Error }));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
