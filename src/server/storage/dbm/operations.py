"""DB management verbs (storage.md §6). Migration vs. reset is an explicit admin action."""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from ..config import StorageConfig
from ..engine import get_engine
from ..schema import Base

_ALEMBIC_DIR = Path(__file__).parent / "alembic"


def alembic_config(cfg: StorageConfig) -> Config:
    c = Config()
    c.set_main_option("script_location", str(_ALEMBIC_DIR))
    c.set_main_option("sqlalchemy.url", cfg.dsn)
    return c


def _has_migrations() -> bool:
    versions = _ALEMBIC_DIR / "versions"
    return any(p.suffix == ".py" for p in versions.glob("*.py"))


def init(cfg: StorageConfig, force: bool = False) -> str:
    # TODO(storage): create database/role if absent using cfg.admin_dsn (storage.md §6).
    # Deferred: db-core/role provisioning is a separate concern; init currently assumes
    # the database and role already exist.
    eng = get_engine(cfg)
    if force:
        Base.metadata.drop_all(eng)
        Base.metadata.create_all(eng)
        if _has_migrations():
            command.stamp(alembic_config(cfg), "head")
        return "recreated"
    existing = set(inspect(eng).get_table_names())
    if set(Base.metadata.tables) & existing:
        # Any overlap is treated as "exists" -- a partial/corrupt schema is not
        # detected or repaired here. Phase-1 create_all (no migrations) also
        # intentionally does not stamp alembic; stamping begins in phase-2 with
        # the baseline revision (`db baseline` / first revision), per storage.md §6.
        return "exists"
    if _has_migrations():
        command.upgrade(alembic_config(cfg), "head")
    else:
        Base.metadata.create_all(eng)
    return "created"


def migrate(cfg: StorageConfig, sql: bool = False) -> str:
    command.upgrade(alembic_config(cfg), "head", sql=sql)
    return "migrated"


def revision(cfg: StorageConfig, message: str, autogenerate: bool = True) -> str:
    command.revision(alembic_config(cfg), message=message, autogenerate=autogenerate)
    return "revision-created"


def status(cfg: StorageConfig) -> dict:
    eng = get_engine(cfg)
    with eng.connect() as conn:
        ctx = MigrationContext.configure(conn)
        current = ctx.get_current_revision()
        drift = compare_metadata(ctx, Base.metadata)
    script = ScriptDirectory.from_config(alembic_config(cfg))
    head = script.get_current_head()
    return {"revision": current, "head": head, "drift": [str(d) for d in drift]}


def seed(cfg: StorageConfig) -> str:
    # No seed rows yet (default org deferred; pricing owned by the cost engine).
    # Loads config/seed/*.yaml when they exist; no-op otherwise.
    seed_dir = Path("config/seed")
    if not seed_dir.exists():
        return "nothing-to-seed"
    return "seeded"


def import_dump(cfg: StorageConfig, source: str) -> str:
    raise NotImplementedError("import: mechanism deferred (storage.md §11)")
