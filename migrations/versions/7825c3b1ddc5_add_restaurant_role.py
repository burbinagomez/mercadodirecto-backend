"""add_restaurant_role

Revision ID: 7825c3b1ddc5
Revises: 
Create Date: 2026-07-09 23:52:59.769773

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7825c3b1ddc5'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 'restaurant' to the user_role enum (PostgreSQL)."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE user_role ADD VALUE 'restaurant'")
    else:
        # SQLite / others: SAEnum is stored as text, no schema change needed.
        pass


def downgrade() -> None:
    """Remove 'restaurant' from user_role enum (PostgreSQL only).

    PostgreSQL does not support removing values from an enum directly.
    We must create a new type, alter the column, and drop the old type.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE TYPE user_role_old AS ENUM ('farmer', 'consumer')")
        op.execute(
            "ALTER TABLE users ALTER COLUMN role TYPE user_role_old "
            "USING role::text::user_role_old"
        )
        op.execute("DROP TYPE user_role")
        op.execute("ALTER TYPE user_role_old RENAME TO user_role")
    else:
        pass
