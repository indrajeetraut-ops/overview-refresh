"""Resolve configured forced-zero periods."""
from datetime import date
from .config import SeriesConfig
from .models import EventEffect, TeamRecord, UserRecord


def _matches(event, user, team, day):
    target = {"user": user.user_id, "team": team.team_name, "org": user.org_id}[event.target_type]
    return event.target == target and event.start_date <= day <= event.end_date


def resolve_event(config: SeriesConfig, user: UserRecord, team: TeamRecord, day: date) -> EventEffect:
    zeros = sorted((event for event in config.events.zero_periods if _matches(event, user, team, day)), key=lambda event: event.id)
    if zeros:
        return EventEffect(forced_zero=True, event_id=zeros[0].id)
    return EventEffect()
