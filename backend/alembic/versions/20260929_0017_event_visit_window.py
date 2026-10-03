"""Schedule-engine case handling, Change 3 (doc B2/B3): the visit window becomes
its own rule on an event, independent of the timing type, so it can be applied to
every occurrence of a repeat and can be one-sided (-0/+3).

The column stays nullable with no default: a window the protocol does not state
must stay unstated (doc B4), never become +/-0 days.

Revision ID: 20260929_0017
Revises: 20260910_0016
"""
from alembic import op
import sqlalchemy as sa

revision = "20260929_0017"
down_revision = "20260910_0016"
branch_labels = None
depends_on = None

_TABLE = "uctsm_events"
_COLUMN = "visit_window"


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    if _COLUMN not in _columns():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    if _COLUMN in _columns():
        op.drop_column(_TABLE, _COLUMN)
