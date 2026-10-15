"""git_artifact_events: PR state-transition history (Track churn/reopen)

Revision ID: b7c31f9a4e02
Revises: a050131eb86f
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7c31f9a4e02'
down_revision = 'a050131eb86f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Append-only PR transition log: `git_artifacts.state` is current-state-only, so a reopen
    # count needs its own history. `source_key` (<artifact_id>:<action>:<occurred_at>) is UNIQUE
    # so delivery replay is a no-op, no `delivery_id` threading needed to dedup.
    op.create_table('git_artifact_events',
    sa.Column('event_id', sa.Integer(), nullable=False),
    sa.Column('artifact_id', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('to_state', sa.String(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source_key', sa.String(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('event_id'),
    sa.UniqueConstraint('source_key'),
    )
    op.create_index(op.f('ix_git_artifact_events_artifact_id'), 'git_artifact_events', ['artifact_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_git_artifact_events_artifact_id'), table_name='git_artifact_events')
    op.drop_table('git_artifact_events')
