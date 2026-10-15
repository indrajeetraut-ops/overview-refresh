"""Shared declarations for all table models (storage.md §3.1)."""
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


ts = Annotated[datetime, mapped_column(DateTime(timezone=True))]   # timestamptz, UTC
jsb = Annotated[dict, mapped_column(JSONB, default=dict)]          # JSONB object/map
jsl = Annotated[list, mapped_column(JSONB, default=list)]          # JSONB list
