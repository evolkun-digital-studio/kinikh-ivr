"""Store provider-hosted recording metadata without downloading caller audio.

Revision ID: 5bb2d9b80aa1
Revises: 1a15ed1a5fc5
"""

from alembic import op
import sqlalchemy as sa

revision = "5bb2d9b80aa1"
down_revision = "1a15ed1a5fc5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_recordings", sa.Column("provider_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("call_recordings", "provider_url")
