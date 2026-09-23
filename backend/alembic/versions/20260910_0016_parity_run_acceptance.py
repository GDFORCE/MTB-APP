"""AD-1a: a DIFFERENCES_FOUND parity run can be explicitly accepted (actor +
reason), so readiness() can pass on MATCH or ACCEPTED instead of a correct,
permanent difference (e.g. ENGINE_UNDATED) leaving the gate unclearable.

Revision ID: 20260910_0016
Revises: 20260901_0015
"""
from alembic import op
import sqlalchemy as sa

revision = "20260910_0016"
down_revision = "20260901_0015"
branch_labels = None
depends_on = None

_TABLE = "uctsm_parity_runs"


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    existing = _columns()
    to_add = []
    if "accepted_at" not in existing:
        to_add.append(sa.Column("accepted_at", sa.DateTime(timezone=True)))
    if "accepted_by" not in existing:
        to_add.append(sa.Column("accepted_by", sa.Uuid()))
    if "accepted_reason" not in existing:
        to_add.append(sa.Column("accepted_reason", sa.Text()))
    for column in to_add:
        op.add_column(_TABLE, column)


def downgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    existing = _columns()
    for name in ("accepted_reason", "accepted_by", "accepted_at"):
        if name in existing:
            op.drop_column(_TABLE, name)
