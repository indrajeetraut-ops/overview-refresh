"""FastAPI wiring for the dashboard read API (dashboard-api-implementation plan, ground rule
1). The only file in this package that imports FastAPI -- everything else is framework-free
and directly testable, the same split `server/uploader/app.py` and `server/ingest/app.py`
already establish.

No `Depends()`-based "current session"/"current catalog" dependency: nothing in this codebase
uses that pattern (see `server/ingest/app.py`). `catalog` is loaded once here and closed over
by route handlers, which call `session_scope()` per request and thread `catalog` into
`service.py` functions explicitly, the same as `worker/rollup.py` and `worker/dispatch.py`.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import structlog
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from common.model_pricing import Pricer
from common.model_pricing.errors import CatalogError
from server.storage.config import StorageConfig
from server.storage.engine import get_engine, session_scope
from server.storage.queries import QueryCatalog

from . import gate
from .config import ApiConfig
from .errors import ApiError, NotFoundError
from .manager import aggregate
from .manager import budget_cycles
from .manager import budgets as budgets_service
from .manager import service as manager_service
from .schemas import (
    BudgetCyclePutResponse,
    BudgetCycleResponse,
    BudgetCycleWriteRequest,
    BudgetForecastEnvelope,
    BudgetPeriodsResponse,
    BudgetPlanEnvelope,
    BudgetResponse,
    BudgetWriteRequest,
    EconomicsEnvelope,
    FeatureDetailEnvelope,
    OrgOverviewEnvelope,
    OrgUsageEnvelope,
    MyTipsEnvelope,
    Overview,
    PersonDetailEnvelope,
    PRDetailEnvelope,
    PricingModelsResponse,
    ProjectDetailEnvelope,
    ProjectsPageEnvelope,
    ScopesResponse,
    TeamDetailEnvelope,
    TeamsPageEnvelope,
)
from .single_user import service

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


log = structlog.get_logger(module="api.app")

#: Path params that identify "who"/"what" a request was about, for the api.request log line.
#: Never payload content -- ids only (ground rule 5).
_LOGGED_PATH_PARAMS = ("id",)


class FeedbackBody(BaseModel):
    """`POST /v1/sessions/{id}/feedback` (A8) -- the one write body this API accepts."""

    beat_ref: str
    verdict: str


class TimezoneBody(BaseModel):
    """`PUT /v1/users/{id}/timezone` (TZ track) -- an IANA zone name, exactly what the
    browser's own `Intl.DateTimeFormat().resolvedOptions().timeZone` returns."""

    timezone: str


def _load_pricing(org: str | None, version: str | None) -> Pricer | None:
    try:
        return Pricer.load(org, version)
    except CatalogError as exc:
        # Survivable, same posture as otel_trueup.py's own loader: /v1/pricing/models serves
        # an empty table rather than the process failing to start. A malformed config
        # (ConfigError) still raises.
        log.warning("pricing_catalog_absent", org=org, version=version, error=str(exc))
        return None


def create_app(config: ApiConfig, storage_config: StorageConfig) -> FastAPI:
    """Route handlers are sync; only middleware and exception handlers are `async` (api-latency
    plan D1/Phase A) -- a `def` handler runs in Starlette's thread pool, so blocking DB calls no
    longer freeze uvicorn's event loop."""
    get_engine(storage_config)  # the process-wide engine session_scope() uses
    catalog = QueryCatalog.load(storage_config.queries_dir)
    pricing = _load_pricing(config.pricing_org, config.pricing_version)

    app = FastAPI()

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        # Populated by routing before call_next returns (Starlette mutates the shared ASGI
        # scope during dispatch) -- reading it here, generically, avoids every handler having
        # to log its own ids by hand.
        ids = {k: v for k, v in request.path_params.items() if k in _LOGGED_PATH_PARAMS}
        log.info("api.request", route=request.url.path, method=request.method,
                 status_code=response.status_code,
                 duration_ms=round((time.monotonic() - start) * 1000, 1), **ids)
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"reason": exc.reason})

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    # Auth seam (BLUE §9 item 3): a FastAPI dependency slots in here without changing any
    # handler's signature. No-op in v1 -- single-user, local/PoC, unauthenticated by explicit
    # decision, not an oversight. Untouched by F2 below -- the manager surface is new routes,
    # not a wrapper around these.

    def _gate_scope(s: DbSession, request: Request, scope_type: str, scope_id: str) -> gate.Viewer:
        """F2: resolve the trusted-header viewer and check it against `(scope_type,
        scope_id)` -- every manager route calls this first. No `Depends()` (ground rule 1's
        no-FastAPI-dependency convention, `server/ingest/app.py`'s precedent): a plain call,
        same shape `session_scope()` already is."""
        viewer = gate.resolve(s, catalog, request.headers, config)
        if scope_type == "team":
            gate.require_team(viewer, scope_id)
        elif scope_type == "repo":
            gate.require_repo(viewer, scope_id)
        else:
            gate.require_manager(viewer)
        return viewer

    def _active_cycle(s: DbSession) -> budget_cycles.ActiveCycle:
        """The org's budget cycle, resolved once per request."""
        return budget_cycles.active(s, catalog, config.directory.default_org_id,
                                    config.budget_period)

    @app.get("/v1/users/{id}/overview", response_model=Overview)
    def get_overview(id: str, to: datetime,
                           from_: datetime = Query(..., alias="from")) -> dict:
        with session_scope() as s:
            return service.overview(s, catalog, config, id, from_, to)

    @app.get("/v1/users/{id}/rhythm")
    def get_rhythm(id: str, weeks: int = 52) -> dict:
        with session_scope() as s:
            return service.rhythm(s, catalog, id, weeks)

    @app.get("/v1/users/{id}/sessions")
    def get_sessions_list(
        id: str, to: datetime, from_: datetime = Query(..., alias="from"),
        sort: str = "recent", vendor: str | None = None, repo_id: str | None = None,
        flags: list[str] | None = Query(None), limit: int = 20, offset: int = 0,
    ) -> dict:
        with session_scope() as s:
            return service.sessions_list(s, catalog, config, id, from_, to, sort, vendor,
                                         repo_id, flags, limit, offset)

    @app.get("/v1/sessions/{id}")
    def get_session_detail(id: str, view: str | None = None) -> dict:
        with session_scope() as s:
            return service.session_detail(s, catalog, config, id, pricing=pricing, view=view)

    @app.get("/v1/users/{id}/insights")
    def get_insights(id: str, to: datetime,
                           from_: datetime = Query(..., alias="from")) -> dict:
        with session_scope() as s:
            return service.insights(s, catalog, config, id, from_, to)

    @app.get("/v1/users/{id}/repos")
    def get_repos(id: str, to: datetime,
                        from_: datetime = Query(..., alias="from")) -> dict:
        with session_scope() as s:
            return service.repos(s, catalog, id, from_, to)

    @app.get("/v1/users/{id}/tips", response_model=MyTipsEnvelope)
    def get_my_tips(
        id: str, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
    ) -> dict:
        # A7's one new piece on top of the shipped single-user surface -- as ungated as the
        # routes above (ground rules section 2's unauthenticated posture, unchanged).
        with session_scope() as s:
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.my_tips(s, catalog, config, id, frm_r, to_r)

    @app.get("/v1/pricing/models", response_model=PricingModelsResponse)
    def get_pricing_models() -> dict:
        # F0 reference port: the response_model types this endpoint into the OpenAPI schema
        # (-> generated TS). The service still returns a plain dict; FastAPI validates it
        # against the model on the way out.
        return service.pricing_models(pricing)

    @app.post("/v1/sessions/{id}/feedback", status_code=201)
    def post_session_feedback(id: str, body: FeedbackBody) -> dict:
        with session_scope() as s:
            return service.submit_feedback(s, catalog, id, body.beat_ref, body.verdict)

    @app.get("/v1/users/{id}/timezone")
    def get_timezone(id: str) -> dict:
        with session_scope() as s:
            return service.get_timezone(s, catalog, id)

    @app.put("/v1/users/{id}/timezone")
    def put_timezone(id: str, body: TimezoneBody) -> dict:
        # As ungated as every other single-user route (ground rules section 2's
        # unauthenticated posture) -- the browser detect-and-confirm flow is this endpoint's
        # only caller.
        with session_scope() as s:
            return service.set_timezone(s, catalog, id, body.timezone)

    # ---------------------------------------------------------- manager surface (F1/F2, Track A)

    # `scope_id:path` -- a repo scope_id is `github:owner/name` (a literal `/`); the default
    # path converter stops at the first `/` and would 404 or mis-split those.
    @app.get("/v1/budgets/{scope_type}/{scope_id:path}", response_model=BudgetResponse)
    def get_budget(scope_type: str, scope_id: str, request: Request) -> dict:
        with session_scope() as s:
            _gate_scope(s, request, scope_type, scope_id)
            active = _active_cycle(s)
            return budgets_service.get_budget(s, catalog, scope_type, scope_id, active.label)

    @app.put("/v1/budgets/{scope_type}/{scope_id:path}", response_model=BudgetResponse)
    def put_budget(scope_type: str, scope_id: str, body: BudgetWriteRequest,
                         request: Request) -> dict:
        with session_scope() as s:
            _gate_scope(s, request, scope_type, scope_id)
            active = _active_cycle(s)
            return budgets_service.put_budget(
                s, catalog, scope_type, scope_id, active.label,
                Decimal(body.amount_usd),
            )

    # The org's budget cadence (Overview refresh A2): one cycle per org, so lead/admin like the
    # org-scope budgets it governs. `/periods` is readable by any resolvable viewer -- the
    # timeframe picker every screen shows needs it, and it carries dates and labels only.
    @app.get("/v1/budget-cycle", response_model=BudgetCycleResponse)
    def get_budget_cycle(request: Request) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_manager(viewer)
            return budget_cycles.get_cycle(s, catalog, config.directory.default_org_id)

    @app.put("/v1/budget-cycle", response_model=BudgetCyclePutResponse)
    def put_budget_cycle(body: BudgetCycleWriteRequest, request: Request) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_manager(viewer)
            cycle = budget_cycles.validate(
                body.kind, body.recurrence, sprint_weeks=body.sprint_weeks,
                anchor_date=body.anchor_date, anchor_number=body.anchor_number,
                custom_end=body.custom_end)
            return budget_cycles.put_cycle(s, catalog, config.directory.default_org_id, cycle)

    @app.get("/v1/budget-cycle/periods", response_model=BudgetPeriodsResponse)
    def get_budget_periods(request: Request, history: int = budget_cycles.DEFAULT_HISTORY) -> dict:
        with session_scope() as s:
            gate.resolve(s, catalog, request.headers, config)
            active = _active_cycle(s)
            return budget_cycles.periods_view(active, datetime.now(timezone.utc), history)

    @app.get("/v1/org/overview", response_model=OrgOverviewEnvelope)
    def get_org_overview(
        request: Request, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
    ) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_manager(viewer)
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.org_overview(s, catalog, config, frm_r, to_r)

    @app.get("/v1/org/usage", response_model=OrgUsageEnvelope)
    def get_org_usage(
        request: Request, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
    ) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_manager(viewer)
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.org_usage(s, catalog, config, frm_r, to_r)

    @app.get("/v1/scopes", response_model=ScopesResponse)
    def get_scopes(request: Request) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_manager(viewer)
            return manager_service.list_scopes(s, catalog, viewer.visible_teams, viewer.visible_repos)

    @app.get("/v1/teams", response_model=TeamsPageEnvelope)
    def get_teams_table(
        request: Request, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
        sort: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
    ) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_manager(viewer)
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.teams_table(s, catalog, config, frm_r, to_r, sort, q, limit, offset)

    @app.get("/v1/projects", response_model=ProjectsPageEnvelope)
    def get_projects_table(
        request: Request, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
        sort: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
    ) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_manager(viewer)
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.projects_table(s, catalog, config, frm_r, to_r, sort, q, limit, offset)

    @app.get("/v1/teams/{id}", response_model=TeamDetailEnvelope)
    def get_team_detail(
        id: str, request: Request, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
    ) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_team(viewer, id)
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.team_detail(s, catalog, config, id, frm_r, to_r)

    @app.get("/v1/people/{id}", response_model=PersonDetailEnvelope)
    def get_person_detail(
        id: str, request: Request, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
    ) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            team_id = manager_service.resolve_person_team(s, catalog, id)
            if team_id is None:
                raise NotFoundError(f"no team on record for {id!r}")
            gate.require_team(viewer, team_id)
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.person_detail(s, catalog, config, id, frm_r, to_r)

    # `id:path` -- a repo_id is `github:owner/name` (a literal `/`); see the budgets route's
    # own note above.
    @app.get("/v1/projects/{id:path}", response_model=ProjectDetailEnvelope)
    def get_project_detail(
        id: str, request: Request, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
    ) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_repo(viewer, id)
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.project_detail(s, catalog, config, id, frm_r, to_r)

    @app.get("/v1/prs/{id}", response_model=PRDetailEnvelope)
    def get_pr_detail(id: str, request: Request) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            session_repo_id = manager_service.resolve_pr_visibility_repo_id(s, catalog, id)
            if session_repo_id is None:
                raise NotFoundError(f"no PR {id!r}")
            gate.require_repo(viewer, session_repo_id)
            return manager_service.pr_detail(s, catalog, config, id)

    # `id:path` -- an H5 branch/label-clustered feature id embeds a real branch or label name
    # (e.g. `branch:<repo_num>:feat/foo`), which commonly contains a literal `/`; see the
    # budgets route's own note above.
    @app.get("/v1/features/{id:path}", response_model=FeatureDetailEnvelope)
    def get_feature_detail(id: str, request: Request) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            session_repo_id = manager_service.resolve_feature_visibility_repo_id(s, catalog, id)
            if session_repo_id is None:
                raise NotFoundError(f"no feature {id!r}")
            gate.require_repo(viewer, session_repo_id)
            return manager_service.feature_detail(s, catalog, config, id)

    @app.get("/v1/economics", response_model=EconomicsEnvelope)
    def get_economics(
        scope_type: str, scope_id: str, request: Request, period: str | None = None,
        to: datetime | None = None, from_: datetime | None = Query(None, alias="from"),
    ) -> dict:
        with session_scope() as s:
            _gate_scope(s, request, scope_type, scope_id)
            frm_r, to_r = aggregate.resolve_timeframe(period, from_, to)
            return manager_service.economics(s, catalog, config, scope_type, scope_id, frm_r, to_r)

    # `scope_id` is a query parameter (not a path segment): a repo scope_id is `github:owner/name`
    # and travels URL-encoded or raw without any `:path` converter.
    @app.get("/v1/forecast", response_model=BudgetForecastEnvelope)
    def get_forecast(scope_type: str, scope_id: str, request: Request) -> dict:
        with session_scope() as s:
            _gate_scope(s, request, scope_type, scope_id)
            return manager_service.budget_forecast(s, catalog, config, scope_type, scope_id)

    @app.get("/v1/forecast/plan", response_model=BudgetPlanEnvelope)
    def get_forecast_plan(request: Request) -> dict:
        with session_scope() as s:
            viewer = gate.resolve(s, catalog, request.headers, config)
            gate.require_manager(viewer)
            return manager_service.budget_plan(s, catalog, config)

    # The built front-end (`src/webui` -> `npm run build`), mounted LAST so it cannot shadow
    # `/v1/*` (MVP plan U0). `html=True` serves index.html for client-side routes. Absent
    # until `vite build` has run -- skipped, not fatal, so the API boots in a bare checkout
    # and in tests.
    dist = Path(config.webui_dist)
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="webui")
    else:
        log.info("webui_bundle_absent", path=str(dist))

    return app
