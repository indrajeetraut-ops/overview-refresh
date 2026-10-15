"""Two host routes for the git-source module, mounted on the real ingest app
(`src/server/ingest/app.py`, git-source.md §3), not a standalone process:

- **The webhook receiver** (§4.1, §5.1) -- graduated (implementation plan A3) from the POC's
  JSONL sink to the real `git_deliveries` landing, ringing the `git_map` worker topic on
  `queue_jobs`. Four jobs and nothing else, **in this order**: cap the size, read the RAW body
  (the HMAC is over raw bytes, so no pydantic body model), verify the signature, land + ring.
  Everything interpretive (mapping a delivery to artifacts) is deliberately absent here -- that
  is the `git_map` worker's job (A4), which is what lets this handler answer inside GitHub's
  delivery timeout regardless of mapper cost or bugs.

  Responses follow git-source.md §5.1, and failure reasons never echo the payload:
      202   verified and landed (a duplicate delivery id is the same answer -- no data lost, no
            second ring: `land_delivery` only rings when it says the row is newly landed)
      204   ping event: verified, nothing to land
      401   missing/invalid signature -- logged content-free, payload discarded
      413   body over the cap
      503   storage unreachable -- GitHub's own delivery log is the retry surface (§10); the
            land and the ring happen in one transaction, so a failure here leaves no partial row

- **The tenant-binding setup callback** (§6.1 mechanism 1, implementation plan A7): GitHub
  redirects here after a "Connect GitHub" install with `installation_id` and our own signed
  `state`. Binds a *pending* org for that installation_id (`git_pending_installs`) -- never
  writes `git_installations` directly, because the redirect's `installation_id` is spoofable
  (state_token.py's docstring); only the signed webhook or a reconcile pass adopts it for real.
"""
from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog

from .config import GithubConfig
from .state_token import verify_state
from .verify import verify

log = structlog.get_logger(module="ingest.git.routes")


def make_router(config: GithubConfig, catalog: QueryCatalog) -> APIRouter:
    router = APIRouter()

    @router.post(config.webhook_path)
    async def receive(request: Request) -> Response:
        body = await request.body()          # raw bytes, before any parsing
        if len(body) > config.max_body_bytes:
            log.warning("delivery_rejected", reason="too_large", size=len(body))
            return JSONResponse({"error": "too_large"}, status_code=413)

        signature = request.headers.get("X-Hub-Signature-256")
        if not verify(config.webhook_secret, body, signature):
            # Content-free: no size, no body, just the reason.
            log.warning("delivery_rejected", reason="bad_signature")
            return JSONResponse({"error": "bad_signature"}, status_code=401)

        event = request.headers.get("X-GitHub-Event", "")
        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        if event == "ping":
            log.info("ping_received", delivery_id=delivery_id)
            return Response(status_code=204)

        payload = json.loads(body)           # safe now: signature already proved integrity
        try:
            with session_scope() as s:
                landed = s.execute(catalog.get("git", "land_delivery"), {
                    "delivery_id": delivery_id,
                    "event": event,
                    "action": payload.get("action"),
                    "installation_id": (payload.get("installation") or {}).get("id"),
                    "repo_num": (payload.get("repository") or {}).get("id"),
                    "payload": json.dumps(payload),
                }).fetchall()
                if landed:   # newly landed; a redelivery of a known id rings nothing again
                    s.execute(catalog.get("git", "ring_git_map"), {"delivery_id": delivery_id})
        except OperationalError:
            # Storage unreachable: GitHub's own delivery log records the failure for manual/API
            # redelivery (§10) -- we must not claim `202` for a delivery we never durably landed.
            log.error("delivery_storage_unavailable", delivery_id=delivery_id, gh_event=event)
            return JSONResponse({"error": "unavailable"}, status_code=503)

        log.info("delivery_received", delivery_id=delivery_id, gh_event=event,
                 action=payload.get("action"),
                 repo_num=(payload.get("repository") or {}).get("id"))
        return JSONResponse({"status": "accepted"}, status_code=202)

    @router.get(config.setup_path)
    async def setup_callback(request: Request) -> Response:
        installation_id_raw = request.query_params.get("installation_id")
        state = request.query_params.get("state")
        if not installation_id_raw or not state:
            return JSONResponse({"error": "missing_params"}, status_code=400)
        try:
            installation_id = int(installation_id_raw)
        except ValueError:
            return JSONResponse({"error": "bad_installation_id"}, status_code=400)

        org_id = verify_state(state, config.state_secret)
        if org_id is None:
            # Content-free, same posture as a bad webhook signature: no hint about why.
            log.warning("setup_state_invalid")
            return JSONResponse({"error": "invalid_state"}, status_code=400)

        try:
            with session_scope() as s:
                s.execute(catalog.get("git", "bind_pending_install"),
                         {"installation_id": installation_id, "org_id": org_id})
        except OperationalError:
            log.error("setup_storage_unavailable", installation_id=installation_id)
            return JSONResponse({"error": "unavailable"}, status_code=503)

        log.info("setup_pending_bound", installation_id=installation_id, org_id=org_id)
        return JSONResponse({"status": "pending", "installation_id": installation_id}, status_code=202)

    return router
