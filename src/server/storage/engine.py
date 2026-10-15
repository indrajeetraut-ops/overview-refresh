"""Process-wide engine and the session_scope transaction boundary (storage.md §4)."""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import StorageConfig

_engine: Engine | None = None
_Session: sessionmaker | None = None


def get_engine(cfg: StorageConfig | None = None) -> Engine:
    global _engine, _Session
    if _engine is None:
        if cfg is None:
            raise RuntimeError("get_engine: first call requires a StorageConfig")
        _engine = create_engine(
            cfg.dsn,
            pool_size=cfg.pool_size,
            max_overflow=cfg.max_overflow,
            pool_timeout=cfg.pool_timeout_s,
            pool_recycle=cfg.pool_recycle_s,
            pool_pre_ping=cfg.pool_pre_ping,
            connect_args=cfg.connect_args,
            echo=cfg.echo_sql,
        )
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        raise RuntimeError("session_scope: call get_engine(cfg) first")
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def dispose_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


@contextmanager
def no_engine() -> Iterator[None]:
    """Run a block with the process engine temporarily cleared, then restored. For code paths
    that build the app only to introspect it (the OpenAPI export, F0) -- `create_app` calls
    `get_engine`, and without this that call would clobber a real engine another caller in the
    same process (a test session) already set up."""
    global _engine, _Session
    saved = (_engine, _Session)
    _engine, _Session = None, None
    try:
        yield
    finally:
        if _engine is not None:
            _engine.dispose()
        _engine, _Session = saved
