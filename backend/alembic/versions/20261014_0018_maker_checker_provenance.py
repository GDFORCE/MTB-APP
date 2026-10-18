"""P3-04: maker-checker provenance on schedule versions.

Adds who last MATERIALLY edited a draft and when, and the distinct set of
material editors. All nullable with no backfill: drafts edited before this
migration have unknown authorship, which is represented as "no provenance" and
handled conservatively at approval - it is never rewritten, and approved
versions are not touched.

Revision ID: 20261014_0018
Revises: 20260929_0017
"""
from alembic import op
import sqlalchemy as sa

revision = "20261014_0018"
down_revision = "20260929_0017"
branch_labels = None
depends_on = None

_TABLE = "uctsm_schedule_versions"
_COLUMNS = {
    "last_material_editor_id": lambda: sa.Column("last_material_editor_id", sa.Uuid(), nullable=True),
    "last_material_edit_at": lambda: sa.Column("last_material_edit_at", sa.DateTime(timezone=True), nullable=True),
    "material_editor_ids": lambda: sa.Column("material_editor_ids", sa.JSON(), nullable=True),
}


def _existing() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    present = _existing()
    for name, column in _COLUMNS.items():
        if name not in present:
            op.add_column(_TABLE, column())


def downgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    present = _existing()
    for name in _COLUMNS:
        if name in present:
            op.drop_column(_TABLE, name)
