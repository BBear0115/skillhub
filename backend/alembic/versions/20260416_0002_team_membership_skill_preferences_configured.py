"""team membership skill preference configured flag

Revision ID: 20260416_0002
Revises: 20260415_0001
Create Date: 2026-04-16 15:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0002"
down_revision: Union[str, None] = "20260415_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "team_memberships",
        sa.Column("skill_preferences_configured", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("team_memberships", "skill_preferences_configured", server_default=None)


def downgrade() -> None:
    op.drop_column("team_memberships", "skill_preferences_configured")
