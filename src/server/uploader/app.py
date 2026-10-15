"""FastAPI wiring for the batch uploader (spec: docs/modules/batch-uploader.md §4).

The only file in this package that imports FastAPI — everything else is
framework-free and directly testable.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common.manifest import DeclaredFields

from . import service
from .asyncbridge import bridge_and_run
from .config import UploaderConfig
from .errors import MalformedArchiveError, UploadTimeoutError, UploadTooLargeError
from .objectstore import make_store


def create_app(config: UploaderConfig) -> FastAPI:
    store = make_store(config.object_store)
    app = FastAPI()

    @app.post("/v1/uploads", status_code=201)
    async def upload(
        request: Request,
        org: str,
        user: str,
        plan: str | None = None,
        vendor: str = "claude_code",
        label: str | None = None,
        account_uuid: str | None = None,
        github_user: str | None = None,
    ):
        declared = DeclaredFields(
            org_id=org, user_id=user, plan=plan, vendor=vendor, label=label,
            account_uuid=account_uuid, github_user=github_user,
        )

        def consume(reader):
            return service.handle_upload(reader, declared, store, config)

        # Auth seam (architecture §18.1, doc §11): a FastAPI dependency slots
        # in here without changing declared->verified semantics downstream.
        # No-op in v1 per the module doc's explicit "thin and unauthenticated"
        # decision.
        try:
            response = await bridge_and_run(
                request, consume, config.limits.upload_timeout_s
            )
        except MalformedArchiveError as exc:
            return JSONResponse(status_code=400, content={"reason": exc.reason})
        except UploadTooLargeError:
            return JSONResponse(status_code=413, content={"reason": "upload_too_large"})
        except UploadTimeoutError:
            return JSONResponse(status_code=499, content={"reason": "upload_timeout"})
        return response

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app
