"""Assign exact Team profile counts once, independently of daily-usage randomness."""
from typing import Sequence
from common.msging.log import get_logger
from .allocation import allocate_integer
from .config import SeriesConfig
from .models import TeamRecord, UserProfileRecord
from .randomness import rng_for


def assign_profiles(config: SeriesConfig, teams: Sequence[TeamRecord], *, log=None) -> list[UserProfileRecord]:
    rows = []
    summary = {}
    for team in sorted(teams, key=lambda item: item.team_id):
        counts = allocate_integer(len(team.user_ids), config.team_profiles[team.team_name].profile_mix)
        members = sorted(team.user_ids)
        rng_for(config.series.profile_seed, "profile", team.team_id).shuffle(members)
        position = 0
        for profile in sorted(counts):
            for uid in members[position:position + counts[profile]]:
                rows.append(UserProfileRecord(user_id=uid, team_id=team.team_id, profile=profile))
            position += counts[profile]
        summary[team.team_name] = counts
    rows.sort(key=lambda row: (row.team_id, row.user_id))
    (log or get_logger(__name__)).info("synthetic.profiles_assigned", team_count=len(teams),
                                    profile_counts=summary, profile_seed=config.series.profile_seed)
    return rows
