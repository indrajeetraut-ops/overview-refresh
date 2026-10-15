"""Config for the git-source acquisition module. Layering: defaults here -> YAML -> env, the
same shape as ingest/config.py (arch §12, git-source.md §11). Secrets are `${VAR}` references
only, never values (CLAUDE.md secret hygiene, arch rule 8).

Graduated (implementation plan A3): the standalone `http` stanza and the POC's `sink_path` are
gone -- the webhook route mounts as a host route on the real ingest app
(`src/server/ingest/app.py`) and lands in `git_deliveries`, not a JSONL file.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_ENV_REF = re.compile(r"\$\{(\w+)\}")


class GithubConfig(BaseModel):
    # --- identity (not secret) ---
    app_id: str = ""                      # numeric App id, for the JWT `iss` fallback
    client_id: str = ""                   # preferred JWT `iss` (GitHub's current recommendation)

    # --- endpoints (GHES-ready from day one, git-source.md Q5) ---
    api_base: str = "https://api.github.com"
    graphql_url: str = "https://api.github.com/graphql"
    api_version: str = "2026-03-10"       # X-GitHub-Api-Version pin (REST only)

    # --- secrets, ${VAR} references resolved from env at load ---
    private_key_path: str = ""            # ${TALIUM_GITHUB_PRIVATE_KEY_PATH}
    webhook_secret: str = ""              # ${TALIUM_GITHUB_WEBHOOK_SECRET}
    # Signs the tenant-binding `state` token (git-source.md §6.1 mechanism 1) -- deliberately
    # its own secret, not `webhook_secret`: one verifies GitHub's inbound deliveries, the other
    # signs our own outbound redirect state, and conflating "which secret does what" is the
    # exact confusion client.py's docstring already warns about for the private key vs. the
    # webhook secret.
    state_secret: str = ""                # ${TALIUM_GITHUB_STATE_SECRET}

    # --- webhook receiver ---
    webhook_path: str = "/talium/git/v1/webhook/github"
    max_body_bytes: int = 25 * 1024 * 1024   # GitHub's own payload cap is 25 MiB

    # --- tenant-binding setup callback (§6.1) ---
    setup_path: str = "/talium/git/v1/setup/github"
    state_ttl_s: int = 900                # 15 min: long enough for a real install click-through

    # --- documentation only: what the App is subscribed to (must match the App's own
    #     registration, git-source.md §7.2 -- this list does not itself change what GitHub sends) ---
    subscribed_events: list[str] = Field(
        default_factory=lambda: [
            "pull_request", "pull_request_review", "push", "check_suite",
            "installation", "installation_repositories",
        ]
    )


def load_config(path: str | Path) -> GithubConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    data = yaml.safe_load(text) or {}
    return GithubConfig.model_validate(data.get("github", data))
