"""abs default library

Revision ID: 9b1f3c2d7e41
Revises: 4e22918d7592
Create Date: 2026-10-11 16:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9b1f3c2d7e41'
down_revision: str | Sequence[str] | None = '4e22918d7592'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('abs_settings') as batch_op:
        batch_op.add_column(sa.Column('default_library_id', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('default_folder_id', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('abs_settings') as batch_op:
        batch_op.drop_column('default_folder_id')
        batch_op.drop_column('default_library_id')
