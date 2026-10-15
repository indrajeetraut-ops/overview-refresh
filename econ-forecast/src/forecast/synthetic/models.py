"""Synthetic-only identities, fixed assignments, events, and operation results."""
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer
from forecast.contracts import CoverageRecord, DailyUsageRecord

Nonempty = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Rec(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)


class UserRecord(Rec):
    user_id: Nonempty
    org_id: Nonempty
    name: Nonempty
    email: Nonempty
    github_login: Nonempty | None = None

    @field_validator("email")
    @classmethod
    def email_shape(cls, value):
        if value.count("@") != 1 or any(char.isspace() for char in value):
            raise ValueError("invalid email shape")
        local, domain = value.split("@")
        if not local or not domain or len(local) > 64:
            raise ValueError("invalid email shape")
        return value

    @model_serializer(mode="wrap")
    def _drop_unset_login(self, handler):
        # Byte-identity with pre-identity-style output (I7): omit github_login entirely when
        # unset, rather than serializing it as null, so independent-style records are unchanged.
        data = handler(self)
        if data.get("github_login") is None:
            data.pop("github_login", None)
        return data


class TeamRecord(Rec):
    team_id: Nonempty
    org_id: Nonempty
    team_name: Nonempty
    user_ids: list[Nonempty] = Field(min_length=1)


class UserProfileRecord(Rec):
    user_id: Nonempty
    team_id: Nonempty
    profile: Nonempty


class ForcedZeroEvent(Rec):
    date: date
    user_id: Nonempty
    team_id: Nonempty
    kind: Literal["forced_zero"] = "forced_zero"
    event_id: Nonempty


class SpikeModelTargetRecord(Rec):
    agent: Nonempty
    model: Nonempty


class SpikeTargetsRecord(Rec):
    teams: list[Nonempty]
    agents: list[Nonempty]
    models: list[SpikeModelTargetRecord]


class SpikeEvent(Rec):
    date: date
    kind: Literal["spike"] = "spike"
    rule_id: Nonempty
    multiplier: float = Field(ge=1)
    targets: SpikeTargetsRecord
    baseline_processed_input: int = Field(gt=0)
    incremental_processed_input: int = Field(gt=0)


class PersonalSpikeEvent(Rec):
    """One user-day scaled by the profile's personal spike (`spike_probability` on a Profile)."""
    date: date
    kind: Literal["personal_spike"] = "personal_spike"
    user_id: Nonempty
    team_id: Nonempty
    profile: Nonempty
    multiplier: float = Field(ge=1)
    baseline_processed_input: int = Field(gt=0)
    incremental_processed_input: int = Field(gt=0)


GenerationEvent: TypeAlias = Annotated[
    ForcedZeroEvent | PersonalSpikeEvent | SpikeEvent,
    Field(discriminator="kind"),
]


class DayBatch(BaseModel):
    date: date
    usage: list[DailyUsageRecord]
    coverage: list[CoverageRecord]
    events: list[GenerationEvent]
    active_users: int = 0
    inactive_users: int = 0
    zero_volume_users: int = 0


class GenerationResult(BaseModel):
    operation: str
    output_dir: Path
    run_id: str
    files: list[str]
    record_counts: dict[str, int]
    summary_counts: dict[str, int]
    report_path: Path


class EventEffect(BaseModel):
    forced_zero: bool = False
    event_id: str | None = None
