"""Schéma initial

Revision ID: 0001
Revises:
"""

from alembic import op

from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Révision initiale : crée l'ensemble des tables déclarées dans app.models.
    # Les évolutions suivantes se font avec `alembic revision --autogenerate`.
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
