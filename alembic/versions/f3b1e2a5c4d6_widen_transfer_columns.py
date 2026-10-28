"""widen transfer columns

Revision ID: f3b1e2a5c4d6
Revises: c8d2e4f5a6b7
Create Date: 2026-10-05 17:58:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3b1e2a5c4d6'
down_revision = 'c8d2e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('calls', 'transfer_number',
               existing_type=sa.String(length=200),
               type_=sa.String(length=255),
               existing_nullable=True)
    op.alter_column('calls', 'transfer_department',
               existing_type=sa.String(length=100),
               type_=sa.String(length=64),
               existing_nullable=True)


def downgrade():
    op.alter_column('calls', 'transfer_department',
               existing_type=sa.String(length=64),
               type_=sa.String(length=100),
               existing_nullable=True)
    op.alter_column('calls', 'transfer_number',
               existing_type=sa.String(length=255),
               type_=sa.String(length=200),
               existing_nullable=True)
