"""directory: identity_links, teams, team_members, role_assignments + users directory fields (G1)

Revision ID: c4e1a9f27d3b
Revises: b7f2a9c14e30
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4e1a9f27d3b'
down_revision = 'b7f2a9c14e30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users: the directory-owned fields (IDENT §3.1). `status` gets a server default so the
    # existing telemetry INSERTs (ingest.yaml `upsert_user_*`), which list columns explicitly
    # and never mention `status`, keep working against the new NOT NULL column.
    op.add_column('users', sa.Column('display_name', sa.String(), nullable=True))
    op.add_column('users', sa.Column('status', sa.String(), nullable=False, server_default='active'))
    op.add_column('users', sa.Column('primary_team_id', sa.String(), nullable=True))
    op.add_column('users', sa.Column('source_provider', sa.String(), nullable=True))

    op.create_table(
        'identity_links',
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('external_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('provider', 'external_id'),
    )
    op.create_index('ix_identity_links_user_id', 'identity_links', ['user_id'])

    op.create_table(
        'teams',
        sa.Column('team_id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('parent_team_id', sa.String(), nullable=True),
        sa.Column('source_provider', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('team_id'),
    )
    op.create_index('ix_teams_org_id', 'teams', ['org_id'])
    op.create_index('ix_teams_parent_team_id', 'teams', ['parent_team_id'])

    op.create_table(
        'team_members',
        sa.Column('team_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('role_in_team', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('team_id', 'user_id'),
    )

    op.create_table(
        'role_assignments',
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('scope_type', sa.String(), nullable=False),
        sa.Column('scope_id', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'role', 'scope_type', 'scope_id'),
    )
    op.create_index('ix_role_assignments_scope', 'role_assignments', ['scope_type', 'scope_id'])


def downgrade() -> None:
    op.drop_index('ix_role_assignments_scope', table_name='role_assignments')
    op.drop_table('role_assignments')
    op.drop_table('team_members')
    op.drop_index('ix_teams_parent_team_id', table_name='teams')
    op.drop_index('ix_teams_org_id', table_name='teams')
    op.drop_table('teams')
    op.drop_index('ix_identity_links_user_id', table_name='identity_links')
    op.drop_table('identity_links')
    op.drop_column('users', 'source_provider')
    op.drop_column('users', 'primary_team_id')
    op.drop_column('users', 'status')
    op.drop_column('users', 'display_name')
