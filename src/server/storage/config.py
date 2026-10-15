"""Storage configuration (storage.md §4, §4.1)."""
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

_ENV_REF = re.compile(r"\$\{([^}]+)\}")


class StorageConfig(BaseModel):
    dsn: str
    admin_dsn: str | None = None
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout_s: int = 30
    pool_recycle_s: int = 1800
    pool_pre_ping: bool = True
    connect_args: dict = {}
    echo_sql: bool = False
    queries_dir: str = "config/queries"


def load_config(path: str | Path) -> StorageConfig:
    text = Path(path).read_text(encoding="utf-8")
    def _replace_env(m):
        value = os.environ.get(m.group(1), "")
        # Quote empty values so YAML parses them as empty strings, not null
        return f'"{value}"' if not value else value
    text = _ENV_REF.sub(_replace_env, text)
    data = yaml.safe_load(text) or {}
    return StorageConfig.model_validate(data.get("storage", data))
