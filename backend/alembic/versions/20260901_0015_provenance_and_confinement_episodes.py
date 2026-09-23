"""AD-8: every materialized patient row carries provenance, and confinement
episodes are a real persisted table instead of only a transient evaluation
result.

Revision ID: 20260901_0015
Revises: 20260901_0014
"""
from alembic import op
import sqlalchemy as sa

from app.db.base import Base
from app.db import models  # noqa: F401

revision = "20260901_0015"
down_revision = "20260901_0014"
branch_labels = None
depends_on = None

_PROVENANCE_TABLES = ("uctsm_patient_events", "uctsm_patient_activities")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    dialect = op.get_bind().dialect.name
    for table in _PROVENANCE_TABLES:
        if table not in tables:
            continue
        existing = _columns(table)
        to_add = []
        if "schedule_version_id" not in existing:
            to_add.append(sa.Column(
                "schedule_version_id", sa.Uuid(),
                sa.ForeignKey(
                    "uctsm_schedule_versions.id",
                    name=f"fk_{table}_schedule_version_id_uctsm_schedule_versions"),
                nullable=True))
        if "protocol_version_id" not in existing:
            to_add.append(sa.Column(
                "protocol_version_id", sa.Uuid(),
                sa.ForeignKey(
                    "uctsm_protocol_versions.id",
                    name=f"fk_{table}_protocol_version_id_uctsm_protocol_versions"),
                nullable=True))
        if not to_add:
            continue
        if dialect == "sqlite":
            # A plain ADD COLUMN can't carry an inline FK on SQLite; batch
            # mode rebuilds the table instead (mirrors downgrade()'s DROP
            # COLUMN branch, which needs it for the same underlying reason).
            # Batch mode requires the new constraint to be named explicitly -
            # unlike a fresh CREATE TABLE, it cannot leave it for the
            # database to auto-name.
            with op.batch_alter_table(table) as batch:
                for column in to_add:
                    batch.add_column(column)
        else:
            for column in to_add:
                op.add_column(table, column)

    if "uctsm_patient_confinement_episodes" not in tables:
        # Metadata is the single declarative schema contract - the same
        # checkfirst create_all pattern 20260829_0001 uses, since this table
        # has no prior version to migrate data from.
        Base.metadata.tables["uctsm_patient_confinement_episodes"].create(
            bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    tables = _tables()
    if "uctsm_patient_confinement_episodes" in tables:
        op.drop_table("uctsm_patient_confinement_episodes")
    dialect = op.get_bind().dialect.name
    for table in _PROVENANCE_TABLES:
        if table not in tables:
            continue
        existing = _columns(table)
        to_drop = [
            name for name in ("protocol_version_id", "schedule_version_id")
            if name in existing
        ]
        if not to_drop:
            continue
        if dialect == "sqlite":
            # A plain DROP COLUMN can't remove a column that is part of a FK
            # definition on SQLite; batch mode rebuilds the table instead.
            # Each dropped column's own index has to go first - batch mode's
            # table rebuild otherwise still tries to recreate it afterwards.
            indexes = sa.inspect(op.get_bind()).get_indexes(table)
            with op.batch_alter_table(table) as batch:
                for name in to_drop:
                    for index in indexes:
                        if index["column_names"] == [name]:
                            batch.drop_index(index["name"])
                    batch.drop_column(name)
        else:
            for name in to_drop:
                op.drop_column(table, name)
