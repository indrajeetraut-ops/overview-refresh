"""Select a stable roster without replacement, deriving internal Team IDs."""
import uuid
from typing import Sequence
from common.msging.log import get_logger
from .config import RosterConfig, validate_users
from .errors import InputDataError
from .models import TeamRecord, UserRecord
from .randomness import rng_for


def create_roster(config: RosterConfig, users: Sequence[UserRecord], *, log=None) -> list[TeamRecord]:
    org = validate_users(users)
    if sum(team.count for team in config.roster.teams) > len(users):
        raise InputDataError("requested Team membership exceeds user pool")
    shuffled = sorted(user.user_id for user in users)
    rng_for(config.roster.seed, "roster", org).shuffle(shuffled)
    rows = []
    position = 0
    for team in sorted(config.roster.teams, key=lambda item: (item.team_name.casefold(), item.team_name)):
        team_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"forecast:team:{org}:{team.team_name.casefold()}"))
        members = sorted(shuffled[position:position + team.count])
        rows.append(TeamRecord(team_id=team_id, org_id=org, team_name=team.team_name, user_ids=members))
        position += team.count
    if len({team.team_id for team in rows}) != len(rows):
        raise InputDataError("duplicate final Team IDs")
    (log or get_logger(__name__)).info("synthetic.roster_created", team_count=len(rows),
        pool_size=len(users), assigned=position, unassigned=len(users) - position, seed=config.roster.seed)
    return rows
