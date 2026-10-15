"""`directory.sync` structured logging (implementation plan G7): counts are present, PII
values are not. DB-gated (sync runs real queries)."""
from structlog.testing import capture_logs

from server.directory import (
    DirectoryConfig,
    DirectoryRecord,
    ExternalKey,
    Precedence,
    TeamMembership,
    TeamRecord,
    sync,
)
from server.storage.engine import session_scope

from .conftest import requires_db

_PII_EMAIL = "alice.private@secret.example"
_PII_NAME = "Alice Confidential"

_RECORDS = [
    DirectoryRecord(
        external_keys=(ExternalKey("csv", _PII_EMAIL), ExternalKey("github", "alicep")),
        email=_PII_EMAIL, display_name=_PII_NAME,
        teams=(TeamMembership("platform", "lead"),), source_provider="csv",
    ),
    # a record that will be skipped -- its key id must also stay out of the log
    DirectoryRecord(
        external_keys=(ExternalKey("csv", "ghost.private@secret.example"),),
        source_provider="csv",
    ),
]
_TEAMS = [TeamRecord("platform", "acme", "Platform", None, "csv")]


@requires_db
def test_sync_log_carries_counts_and_no_pii(engine, catalog):
    with capture_logs() as logs:
        with session_scope() as s:
            sync(s, catalog, _RECORDS, teams=_TEAMS,
                 precedence=Precedence(), config=DirectoryConfig())

    evt = next(e for e in logs if e["event"] == "directory.sync")

    for key in ("created", "updated", "linked", "skipped", "providers",
                "records", "teams", "duration_ms"):
        assert key in evt, f"missing {key} in directory.sync log"
    assert evt["created"] == 1
    assert evt["skipped"] == 1
    assert evt["records"] == 2
    assert evt["teams"] == 1
    assert evt["providers"] == ["csv"]

    blob = repr(evt)
    for secret in (_PII_EMAIL, _PII_NAME, "Confidential", "ghost.private@secret.example"):
        assert secret not in blob, f"{secret!r} leaked into the directory.sync log"
