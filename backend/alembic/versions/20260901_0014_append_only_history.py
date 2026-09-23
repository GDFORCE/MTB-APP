"""AD-9: patient clinical history is append-only.

A patient delete must be blocked while history exists, not silently take the
history with them - so the five patient-history FK constraints that used to
cascade are recreated without ``ON DELETE CASCADE`` here (they already read
that way in ``db/models.py``; this migration brings an already-deployed
database's actual constraints in line with it). On PostgreSQL a trigger also
rejects any UPDATE/DELETE against the append-only history tables outright,
matching the immutability trigger pattern in ``20260829_0001``.

This trigger is PostgreSQL-only and is NOT exercised by the current test
harness, which runs entirely against ad hoc SQLite ``create_all()`` and never
runs Alembic. That is a disclosed gap, not a silent one: the FK-ondelete
change is verified (it is inspectable without a real Postgres server), the
trigger's actual rejection behaviour is not.

Revision ID: 20260901_0014
Revises: 20260901_0013
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_0014"
down_revision = "20260901_0013"
branch_labels = None
depends_on = None

#: (table, fk constraint name, local column) for the five patient-history
#: tables named by AD-9. The constraint name is Postgres's own default
#: single-column FK naming (none of these were given an explicit ``name=``),
#: so it is exactly what a database created by ``20260829_0001`` already has.
_HISTORY_FKS = [
    ("uctsm_patient_anchors", "uctsm_patient_anchors_patient_id_fkey", "patient_id"),
    ("uctsm_patient_states", "uctsm_patient_states_patient_id_fkey", "patient_id"),
    ("uctsm_patient_unscheduled_visits",
     "uctsm_patient_unscheduled_visits_patient_id_fkey", "patient_id"),
    ("uctsm_patient_conditions", "uctsm_patient_conditions_patient_id_fkey", "patient_id"),
    ("uctsm_patient_activity_records",
     "uctsm_patient_activity_records_patient_id_fkey", "patient_id"),
]

#: Append-only history tables an UPDATE/DELETE must never be allowed to touch.
_APPEND_ONLY_TABLES = [
    "uctsm_audit_events",
    "uctsm_patient_activity_records",
    "uctsm_patient_conditions",
    "uctsm_patient_anchors",
]


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


#: SQLite never names a FK constraint created without an explicit ``name=``
#: (``sa.inspect(...).get_foreign_keys(...)`` reports ``name: None`` for all
#: five of these), so there is no real name ``drop_constraint`` could target.
#: This convention lets Alembic's batch mode assign one deterministically
#: during reflection, purely so the very next line can refer to it - the
#: documented workaround for altering an unnamed constraint in SQLite.
_SQLITE_FK_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    tables = _tables()
    dialect = op.get_bind().dialect.name

    for table, fk_name, column in _HISTORY_FKS:
        if table not in tables:
            continue
        if dialect == "sqlite":
            # SQLite has no ALTER for FK constraints; batch mode rebuilds the
            # table. This branch only ever runs against a real file DB - the
            # SQLite-only test harness never runs Alembic at all (it uses
            # ad hoc create_all() instead).
            generated_name = f"fk_{table}_{column}_uctsm_patients"
            with op.batch_alter_table(
                table, naming_convention=_SQLITE_FK_NAMING_CONVENTION,
            ) as batch:
                batch.drop_constraint(generated_name, type_="foreignkey")
                batch.create_foreign_key(
                    fk_name, "uctsm_patients", [column], ["id"])
        else:
            op.drop_constraint(fk_name, table, type_="foreignkey")
            op.create_foreign_key(
                fk_name, table, "uctsm_patients", [column], ["id"])

    if dialect == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION uctsm_reject_history_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'UCTSM patient clinical history is append-only';
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """)
        for table in _APPEND_ONLY_TABLES:
            if table not in tables:
                continue
            op.execute(f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION uctsm_reject_history_mutation()
            """)


def downgrade() -> None:
    tables = _tables()
    dialect = op.get_bind().dialect.name

    if dialect == "postgresql":
        for table in _APPEND_ONLY_TABLES:
            if table in tables:
                op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS uctsm_reject_history_mutation() CASCADE")

    for table, fk_name, column in _HISTORY_FKS:
        if table not in tables:
            continue
        if dialect == "sqlite":
            # By this point the constraint already carries the explicit name
            # ``upgrade()`` gave it, so no naming convention is needed here.
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(fk_name, type_="foreignkey")
                batch.create_foreign_key(
                    fk_name, "uctsm_patients", [column], ["id"], ondelete="CASCADE")
        else:
            op.drop_constraint(fk_name, table, type_="foreignkey")
            op.create_foreign_key(
                fk_name, table, "uctsm_patients", [column], ["id"], ondelete="CASCADE")
