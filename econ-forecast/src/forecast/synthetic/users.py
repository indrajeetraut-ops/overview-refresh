"""Seeded fake identities with stable IDs and deterministic email collision handling."""
import re
import unicodedata
import uuid
from faker import Faker
from common.msging.log import get_logger
from .config import UsersConfig
from .errors import InputDataError
from .models import UserRecord
from .randomness import derive_seed


def _local(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9._-]", "", value).strip("._-") or "user"


def _github_login(first: str, last: str, seen_logins: set[str]) -> str:
    base = (re.sub(r"[^a-z0-9-]", "", _local(first) + _local(last)[:1]) or "user")[:39]
    login = base
    suffix_index = 1
    while login in seen_logins:
        suffix_index += 1
        suffix = f"-{suffix_index}"
        login = (base[:39 - len(suffix)] + suffix)[:39]
    seen_logins.add(login)
    return login


def create_users(config: UsersConfig, *, log=None) -> list[UserRecord]:
    options = config.users
    consistent = options.identity_style == "consistent"
    seen_emails, seen_ids, seen_logins = set(), set(), set()
    rows = []
    fake = Faker(options.locale)
    for index in range(1, options.count + 1):
        fake.seed_instance(derive_seed(options.seed, "identity", options.org_domain, str(index)))
        github_login = None
        if consistent:
            first, last = fake.first_name(), fake.last_name()
            display_name = f"{first} {last}"
            base = _local(f"{first}.{last}")[:64]
            github_login = _github_login(first, last, seen_logins)
        else:
            display_name = fake.name()
            base = _local(fake.user_name())[:64]
        local = base
        suffix_index = 1
        while f"{local}@{options.org_domain}" in seen_emails:
            suffix_index += 1
            suffix = f"_{suffix_index}"
            local = base[:64 - len(suffix)] + suffix
        email = f"{local}@{options.org_domain}"
        user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"forecast:user:{options.org_domain}:{index}"))
        if user_id in seen_ids:
            raise InputDataError("generated user ID collision")
        seen_emails.add(email)
        seen_ids.add(user_id)
        rows.append(UserRecord(user_id=user_id, org_id=options.org_domain, name=display_name, email=email,
                               github_login=github_login))
    rows.sort(key=lambda user: user.user_id)
    (log or get_logger(__name__)).info("synthetic.users_created", user_count=len(rows),
                                    locale=options.locale, seed=options.seed)
    return rows
