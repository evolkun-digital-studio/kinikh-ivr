"""add missing call columns

Revision ID: c8d2e4f5a6b7
Revises: b7c1d2e3f4a5
Create Date: 2026-10-05 17:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = 'c8d2e4f5a6b7'
down_revision = 'b7c1d2e3f4a5'
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    columns = [c['name'] for c in inspector.get_columns('calls')]
    
    if 'reference' not in columns:
        op.add_column('calls', sa.Column('reference', sa.String(50), nullable=True))
    
    # Check if index exists before creating it
    indexes = [i['name'] for i in inspector.get_indexes('calls')]
    if 'ix_calls_reference' not in indexes:
        op.create_index('ix_calls_reference', 'calls', ['reference'], unique=True)

def downgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    
    indexes = [i['name'] for i in inspector.get_indexes('calls')]
    if 'ix_calls_reference' in indexes:
        op.drop_index('ix_calls_reference', table_name='calls')
        
    columns = [c['name'] for c in inspector.get_columns('calls')]
    if 'reference' in columns:
        op.drop_column('calls', 'reference')
