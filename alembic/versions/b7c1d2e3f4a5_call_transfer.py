"""Record live call transfers on the call.

Revision ID: b7c1d2e3f4a5
Revises: e9c0c9ce7998
"""

from alembic import op
import sqlalchemy as sa

revision = "b7c1d2e3f4a5"
down_revision = "e9c0c9ce7998"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column(
            "transfer_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("calls", sa.Column("transfer_department", sa.String(100), nullable=True))
    op.add_column("calls", sa.Column("transfer_number", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "transfer_number")
    op.drop_column("calls", "transfer_department")
    op.drop_column("calls", "transfer_requested")
