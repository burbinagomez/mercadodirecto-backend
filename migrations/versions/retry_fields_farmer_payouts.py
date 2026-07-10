"""add retry and error-tracking columns to farmer_payouts

Revision ID: retry_fields_farmer_payouts
Revises: aa4abbeda401
Create Date: 2026-07-10 12:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "retry_fields_farmer_payouts"
down_revision: Union[str, Sequence[str], None] = "aa4abbeda401"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add retry-supporting columns to farmer_payouts."""
    op.add_column(
        "farmer_payouts",
        sa.Column("payment_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_farmer_payouts_payment_id"),
        "farmer_payouts",
        ["payment_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_farmer_payouts_payment_id",
        "farmer_payouts",
        "payments",
        ["payment_id"],
        ["id"],
    )
    op.add_column(
        "farmer_payouts",
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "farmer_payouts",
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "farmer_payouts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove retry-supporting columns from farmer_payouts."""
    op.drop_column("farmer_payouts", "updated_at")
    op.drop_column("farmer_payouts", "retry_count")
    op.drop_column("farmer_payouts", "error_message")
    op.drop_constraint("fk_farmer_payouts_payment_id", "farmer_payouts", type_="foreignkey")
    op.drop_index(op.f("ix_farmer_payouts_payment_id"), table_name="farmer_payouts")
    op.drop_column("farmer_payouts", "payment_id")
