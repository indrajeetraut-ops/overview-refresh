"""The upload manifest wire contract -- shared between both uploader generations that write
one and the collector that reads it (uploader/collector integration plan §B1/§B2).

Moved here from `server/uploader/models.py`: the collector used to import its wire types from
the *old Python uploader's* module, which stopped being accurate once a second producer (the
Go `talium-sync` client, `../upload`) started writing manifests too. `server.uploader.models`
now imports these three types back from here -- it still owns `UploadResponse`, which has no
second producer.

Two manifest generations on the wire, distinguished by which identifier they carry:
  * the old Python batch uploader (`src/server/uploader/`) writes `upload_id`;
  * the new Go uploader (`../upload`) writes `endpoint_id`, and adds `vendors` plus a set of
    new per-file fields (vendor/session_id/repo/repo_url/branch/state/active/mtime), all
    optional so an old-generation manifest still validates unchanged.
`scope_id` is the one thing a caller actually needs: whichever id this generation wrote.
"""
from __future__ import annotations

from pydantic import BaseModel, model_validator


class DeclaredFields(BaseModel):
    """Identity/context the JSONL files don't carry, self-declared at upload time."""

    org_id: str
    user_id: str
    plan: str | None = None
    # Old-uploader-generation compatibility only: the new Go uploader's manifest carries
    # vendor per file (see ManifestFileEntry.vendor), never here. Nothing may read this field
    # for a new-generation manifest -- group() falls back to "claude_code" per file instead.
    vendor: str = "claude_code"
    label: str | None = None
    # The vendor account identity OTel carries in `user.account_uuid` (a different namespace
    # than `user_id`) -- optional, so an older collector or a non-OAuth setup still uploads
    # fine. Read locally from the Claude Code OAuth credentials by
    # utils/upload_claude_sessions.sh; carried through so an otel_only session can resolve to
    # this same `user_id` instead of surfacing the raw account UUID (otel_trueup.py).
    account_uuid: str | None = None
    # Self-declared GitHub login, optional -- lets the ingest fold handler pre-populate
    # `users.github_user` for this `user_id` (pr-work-classification identity-linking), so
    # git-source's own `resolve_user_id` finds this declared mapping instead of auto-creating
    # a separate `github:<login>` identity the first time this person's PR lands.
    github_user: str | None = None


class ManifestFileEntry(BaseModel):
    key: str  # S3 key relative to the upload prefix, e.g. raw/projects/<session>.jsonl
    rel: str  # archive-relative path within ~/.claude/projects
    bytes: int
    sha256: str | None = None  # absent in v1 (doc §12 U3)
    # Everything below is new-Go-uploader-generation only; None on an old-generation manifest.
    vendor: str | None = None  # None => old uploader => claude_code (collector's group() default)
    session_id: str | None = None
    repo: str | None = None  # normalized "owner/repo"
    repo_url: str | None = None  # raw remote URL; preferred over repo when present
    branch: str | None = None
    state: str | None = None  # "live" | "stale" -- not authoritative for "already collected"
    active: bool = False
    mtime: str | None = None


class Manifest(BaseModel):
    """manifest.json -- written last; the source adapter's doorbell (doc §6)."""

    upload_id: str | None = None  # old Python uploader
    endpoint_id: str | None = None  # new Go uploader
    received_ts: str
    source: str = "batch"
    declared: DeclaredFields
    producer_id: str
    file_count: int
    files: list[ManifestFileEntry]
    uploader_version: str
    vendors: list[str] = []  # new uploader only

    @model_validator(mode="after")
    def _exactly_one_id(self) -> "Manifest":
        if not (self.upload_id or self.endpoint_id):
            raise ValueError("manifest carries neither upload_id nor endpoint_id")
        return self

    @property
    def scope_id(self) -> str:
        """The per-endpoint/per-upload identifier, whichever generation wrote this."""
        return self.endpoint_id or self.upload_id
