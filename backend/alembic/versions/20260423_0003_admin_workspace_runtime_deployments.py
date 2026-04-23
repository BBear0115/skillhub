"""admin workspace runtime deployments

Revision ID: 20260423_0003
Revises: 20260416_0002
Create Date: 2026-04-23 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260423_0003"
down_revision: Union[str, None] = "20260416_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("skill_versions", sa.Column("deploy_status", sa.String(), nullable=False, server_default="not_deployed"))
    op.add_column("skill_versions", sa.Column("deploy_error", sa.String(), nullable=True))
    op.add_column("skill_versions", sa.Column("runtime_path", sa.String(), nullable=True))
    op.add_column("skill_versions", sa.Column("venv_path", sa.String(), nullable=True))
    op.add_column("skill_versions", sa.Column("dependency_manifest", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("skill_versions", "dependency_manifest")
    op.drop_column("skill_versions", "venv_path")
    op.drop_column("skill_versions", "runtime_path")
    op.drop_column("skill_versions", "deploy_error")
    op.drop_column("skill_versions", "deploy_status")
