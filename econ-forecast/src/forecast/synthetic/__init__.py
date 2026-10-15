"""Independent generator entry points, imported lazily without startup side effects."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import RosterConfig, SeriesConfig, UsersConfig
    from .models import GenerationResult

__all__ = ["run_users", "run_roster", "run_series"]


def run_users(config: UsersConfig, output_dir: Path, *, log=None, run_id=None) -> GenerationResult:
    """Generate reusable synthetic users and their dataset report."""
    from .runner import run_users as execute
    return execute(config, output_dir, log=log, run_id=run_id)


def run_roster(config: RosterConfig, output_dir: Path, *, log=None, run_id=None) -> GenerationResult:
    """Generate fixed Team membership from verified saved users."""
    from .runner import run_roster as execute
    return execute(config, output_dir, log=log, run_id=run_id)


def run_series(config: SeriesConfig, output_dir: Path, *, log=None, run_id=None) -> GenerationResult:
    """Generate daily usage from saved identities and Team membership."""
    from .runner import run_series as execute
    return execute(config, output_dir, log=log, run_id=run_id)
