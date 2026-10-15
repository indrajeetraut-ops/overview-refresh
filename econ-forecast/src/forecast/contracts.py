"""Version-one daily records shared by synthetic and real-data preparation."""
from datetime import date
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

Nonempty = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Record(BaseModel):
    """Readers tolerate additive fields; writers emit the known contract."""
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    @field_validator("date", mode="before", check_fields=False)
    @classmethod
    def calendar_date(cls, value):
        if type(value) is date:
            return value
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        raise ValueError("date must be an ISO calendar date")


class TokenCounts(Record):
    """`input` is uncached input; cache writes are separate. All nullable measures."""
    input: StrictInt | None
    cache_write_5m: StrictInt | None
    cache_write_1h: StrictInt | None
    cache_read: StrictInt | None
    output: StrictInt | None

    @field_validator("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
    @classmethod
    def nonnegative(cls, value):
        if value is not None and value < 0:
            raise ValueError("token counts must be nonnegative")
        return value


class DailyUsageRecord(Record):
    date: date
    org_id: Nonempty
    team_id: Nonempty | None
    user_id: Nonempty | None
    project_id: Nonempty | None
    agent_type: Nonempty
    model: Nonempty
    billing_mode: Literal["api", "subscription", "unknown"]
    tokens: TokenCounts


class CoverageRecord(Record):
    date: date
    org_id: Nonempty
    user_id: Nonempty | None
    agent_type: Nonempty
    status: Literal["complete", "partial", "missing", "unknown"]
