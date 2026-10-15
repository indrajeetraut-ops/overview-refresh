"""Config for the directory module. Layering per arch §12: defaults here -> YAML -> env.

TODO(directory): switch load_config to the shared config loader in src/common when that
loader lands (arch §12 "one shared loader"). Mirrors uploader/ingest config.py verbatim.
"""
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .precedence import DEFAULT_CHAIN

_ENV_REF = re.compile(r"\$\{([^}]+)\}")


class DirectoryConfig(BaseModel):
    #: Provider precedence, highest first (IDENT §3, G3). Adding an IdP above `csv` is a
    #: config edit here, nothing more.
    precedence: list[str] = Field(default_factory=lambda: list(DEFAULT_CHAIN))
    #: Single tenant in MVP; stamped on every team/role row so multi-tenant is a filter
    #: later, not a rewrite (IDENT §7).
    default_org_id: str = "default"
    #: A record with no explicit roles gets this at org scope (IDENT §3.2: "[] = default dev").
    default_role: str = "dev"
    directory_version: str = "0.1.0"


def load_config(path: str | Path) -> DirectoryConfig:
    text = Path(path).read_text(encoding="utf-8")

    def _replace_env(m: re.Match) -> str:
        value = os.environ.get(m.group(1), "")
        return f'"{value}"' if not value else value

    text = _ENV_REF.sub(_replace_env, text)
    data = yaml.safe_load(text) or {}
    return DirectoryConfig.model_validate(data.get("directory", data))
