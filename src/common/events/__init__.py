"""The normalized-event contract: what the collector emits and downstream workers consume.

This package is the shared reference point between the two sides. The collector produces
events (`src/collector/normalizer`); the causal-graph worker and the analytics workers read
them. Neither should have to read the other's source to know the shape, so the shape is
published here -- derived from the normalizer's real output rather than written by hand, so it
cannot drift into fiction (see `schema.py` and `cli_publish_payload.py`).

SCHEMA_VERSION covers the *payload* contract: which event types exist and what fields their
payloads carry. It rides in `meta_info.schema_version` on every normalized dump.

Evolution rules, normative:

  * **Additive-only within a major.** New event types and new payload fields may appear at any
    time. Consumers MUST ignore fields they do not recognise -- an unknown key is forward
    compatibility, not an error.
  * **Removing or retyping a field is a major bump.** So is changing what an existing field
    means, which is the case an additive-only rule does not catch on its own.
  * **`meta_info` itself is versioned by the same number.** A consumer that can read the
    envelope can read the version out of it before deciding whether it understands the rest.

The drift guard (`tests/collector/normalizer/test_shape_contract.py`) fails whenever real
output stops matching the committed schema, which is what keeps this honest: the version can
only be wrong if someone regenerates the schema without bumping it.
"""
from __future__ import annotations

__all__ = ["SCHEMA_VERSION"]

# 3.2.0 -- additive: `session_start.git_remote`. Not derived from the vendor's own JSONL --
# the collector engine backfills it from the upload manifest's per-file `repo_url`/`repo` (a
# real `git remote get-url origin`, captured by the uploader client at the session's actual
# working directory; originally sourced from a separate cwd-keyed sidecar manifest, superseded
# by the per-file field once the new Go uploader made cwd itself unrecoverable from the
# session's directory slug -- see the uploader/collector integration plan, 2026-09-22). Lets
# the causal-graph fold's repo-identity evidence ladder (`worker/graph_fold.py::repo_info`)
# resolve a session to `github:owner/name` even when nothing in the transcript itself ever
# mentions a remote -- previously every session with no PR link and no incidental `git remote`
# in a Bash tool call fell all the way to the `local:<cwd-basename>` tier, which never bridges
# to a connected GitHub repo. The payload shape and field meaning are unchanged by this
# provenance change, so SCHEMA_VERSION does not bump for it.
# 3.1.0 -- additive: `assistant_message.provider_request_id`. Present in the raw record on
# essentially every response already; it was only ever being read on `_make_api_error`'s
# records, so a JSONL-only session's cost estimate (the request-level join in
# `worker/otel_trueup.py`, keyed on this exact field) silently found nothing for any session
# that never errored -- independent of whether OTel telemetry was also running.
# 3.0.0 -- `lines_added`/`lines_removed` removed from `file_change` and `tool_call.files[]`.
# They read a vendor key that is absent on every Edit/Write result (0 of 1,690 checked), so
# they were null on every file the pipeline ever saw; a permanently null field reads as "this
# edit added no lines" rather than "we do not know". Hunks gain `old_len`, so `len - old_len`
# gives the net change -- additive, but the removal makes this major.
#   Also in 3.0.0, all additive: `user_message.permission_mode`/`plan_mode` (the vendor now
#   stamps `permissionMode` on the prompt record, so plan_mode stops being permanently null);
#   `assistant_message.attribution_skill`/`_plugin`/`_agent` and `effort`;
#   `session_start.attribution_agent`. Attribution answers "which skill did this work use",
#   which the `skill_listing` attachment cannot -- it only says what was offered in context.
# 2.0.0 -- `visible_output_tokens` renamed to `estimated_visible_tokens`. A rename is a
# removal plus an addition, which the rules above make a major bump; the name mattered enough
# to break for, because the old one read as a measurement of Claude's tokens when it is an
# estimate from a different vocabulary. Every field also carries a description as of this
# version -- see descriptions.py, which the publisher now enforces.
# 1.0.0 -- first published contract. Eleven event types from the Claude Code normalizer.
SCHEMA_VERSION = "3.2.0"
