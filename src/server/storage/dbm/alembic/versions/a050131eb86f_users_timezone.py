"""users: timezone preference (TZ track, mvp-gap-closure-plan)

Revision ID: a050131eb86f
Revises: 8e59d8946ba4
"""
from alembic import op
import sqlalchemy as sa

revision = 'a050131eb86f'
down_revision = '8e59d8946ba4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable IANA zone name (e.g. "America/Los_Angeles"), browser-detected and
    # self-service-set (single_user's own `set_timezone`, the surface's second write).
    # Null means "no preference recorded yet" -- every UTC-bucketing call site treats that the
    # same as today's behavior, so this column's rollout needs no backfill.
    op.add_column('users', sa.Column('timezone', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'timezone')
