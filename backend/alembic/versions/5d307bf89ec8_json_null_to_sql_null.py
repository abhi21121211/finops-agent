"""json null to sql null

Revision ID: 5d307bf89ec8
Revises: 7739ec01fc81
Create Date: 2026-10-06 12:55:43.001780

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d307bf89ec8'
down_revision: Union[str, Sequence[str], None] = '7739ec01fc81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Convert JSON `null` values (written before none_as_null) to SQL NULL."""
    for col in ('extraction', 'field_confidence', 'validation_issues', 'route_reasons'):
        op.execute(f"UPDATE invoices SET {col} = NULL WHERE {col} = 'null'::jsonb")


def downgrade() -> None:
    """Nothing to undo: SQL NULL is a valid value for these columns either way."""
