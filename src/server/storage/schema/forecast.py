"""forecast_runs / forecast_scope_series -- the precomputed dashboard forecast
(docs/plan/2026-10-05-forecast-dashboard-integration.md §3.5; docs/modules/storage.md §3.8).

One loaded export is one `forecast_runs` row plus one `forecast_scope_series` row per
(scope, preset). The loader (`server.storage.forecast_load`) keeps exactly one run at a time:
it replaces both tables' contents inside one transaction.

No FKs (storage.md §3.1): `forecast_scope_series.run_id` names a `forecast_runs` row by
construction (they are written in the same transaction), and `scope_id` names a team,
repository or org of the directory without being enforced against it.
"""
from datetime import date

from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class ForecastRun(Base):
    """One loaded `forecast_export.json`. `run_id` is the first 16 hex characters of the
    sha256 of the file's bytes. `meta` holds everything run-wide that is not a column:
    `schema`, `timezone`, `band`, `quantile_grid`, `dates`, `periods`, `presets`, `scopes`
    (type, id and display name, in export order), plus any extra top-level key of a newer
    minor version, under its own name."""

    __tablename__ = "forecast_runs"
    run_id: Mapped[str] = mapped_column(primary_key=True)
    origin: Mapped[date]                    # forecast cutoff: history after it is not fitted
    data_through: Mapped[date]              # last day of actual data
    generated_at: Mapped[ts]                # when the export was written (the file's own stamp)
    meta: Mapped[jsb]
    loaded_at: Mapped[ts]                   # database now() at load time


class ForecastScopeSeries(Base):
    """One scope x preset. `payload` is the export's series entry without `status`: `daily`
    and `periods` for a scope with numbers, `{}` for an `incomplete` one."""

    __tablename__ = "forecast_scope_series"
    run_id: Mapped[str] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(primary_key=True)   # org | team | repo
    scope_id: Mapped[str] = mapped_column(primary_key=True)
    preset_id: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]                     # complete | conditional | inactive | incomplete
    payload: Mapped[jsb]
