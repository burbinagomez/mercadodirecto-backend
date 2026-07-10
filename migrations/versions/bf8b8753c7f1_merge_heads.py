"""merge heads

Revision ID: bf8b8753c7f1
Revises: 1ac7349bb5fb, 7825c3b1ddc5
Create Date: 2026-07-10 11:21:54.266444

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf8b8753c7f1'
down_revision: Union[str, Sequence[str], None] = ('1ac7349bb5fb', '7825c3b1ddc5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
