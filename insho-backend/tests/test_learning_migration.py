from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db import database
from conftest import test_engine


def test_learning_migration_keeps_part3_data_and_protects_learning_history(monkeypatch):
    schema = "learning_" + uuid4().hex
    with test_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(test_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(database, "engine", engine)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    identifiers = {name: uuid4() for name in ("event", "other_event", "snapshot", "version", "audit", "actual", "report")}
    identifiers["scope"] = str(identifiers["event"])
    previous = "67e52ccf940a"
    try:
        command.upgrade(config, previous)
        with engine.begin() as connection:
            identifiers["project"], identifiers["org"] = connection.execute(text("""
                WITH org AS (
                    INSERT INTO organizations (id, name, slug) VALUES (gen_random_uuid(), 'Learning fixture', 'learning-fixture')
                    RETURNING id
                ), workspace AS (
                    INSERT INTO workspaces (id, organization_id, name, slug)
                    SELECT gen_random_uuid(), id, 'Fixture', 'fixture' FROM org RETURNING id, organization_id
                )
                INSERT INTO projects (id, organization_id, workspace_id, name, status)
                SELECT gen_random_uuid(), organization_id, id, 'Existing project', 'draft' FROM workspace RETURNING id, organization_id
            """)).one()
            connection.execute(text("""
                INSERT INTO events (id, organization_id, project_id, name)
                VALUES (:event, :org, :project, 'Selected event'), (:other_event, :org, :project, 'Second event')
            """), identifiers)
            connection.execute(text("""
                INSERT INTO design_snapshots (id, project_id, scope_key, state)
                VALUES (:snapshot, :project, :scope, jsonb_build_object('title', 'Existing snapshot'))
            """), identifiers)
            connection.execute(text("""
                INSERT INTO design_versions (id, project_id, event_id, scope_key, snapshot_id, number, summary, reason)
                VALUES (:version, :project, :event, :scope, :snapshot, 1, 'Existing version', 'Historical state')
            """), identifiers)
            connection.execute(text("""
                INSERT INTO design_audits (id, organization_id, project_id, event_id, design_version_id, snapshot_id, request_id,
                                           status, configuration_version, options, input_fingerprint, results, warnings, retryable, evidence_sources)
                VALUES (:audit, :org, :project, :event, :version, :snapshot, gen_random_uuid(), 'running', 'fixture',
                        '{}', repeat('1', 64), '[]', '[]', false, '[]')
            """), identifiers)
        command.upgrade(config, "head")
        command.check(config)
        actual = text("""
            INSERT INTO learning_actuals (id, organization_id, project_id, event_id, design_version_id, snapshot_id,
                                          kind, revision, status, origin, data, sources, derived, occurred_on, review_note, verified_at)
            VALUES (:actual, :org, :project, :event, :version, :snapshot, 'delay', 2, 'verified', 'reported',
                    jsonb_build_object('kind', 'delay'), '[]', '{}', CURRENT_DATE, 'Verified record', now())
        """)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(actual, {**identifiers, "event": identifiers["other_event"]})
        with engine.begin() as connection:
            connection.execute(actual, identifiers)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE learning_actuals SET data = '{}' WHERE id = :actual"), identifiers)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("DELETE FROM learning_actuals WHERE id = :actual"), identifiers)
        report = text("""
            INSERT INTO learning_reports (id, organization_id, project_id, kind, event_ids, request_fingerprint, input_fingerprint,
                                          method_version, content, warnings)
            VALUES (:report, :org, :project, 'insights', jsonb_build_array(CAST(:scope AS text)), repeat('1',64), repeat('2',64), 'fixture', '{}', '[]')
        """)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(report, {**identifiers, "scope": str(uuid4())})
        with engine.begin() as connection:
            connection.execute(report, identifiers)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE learning_reports SET warnings = '[\"replaced\"]' WHERE id = :report"), identifiers)
        command.downgrade(config, previous)
        assert not inspect(engine).has_table("learning_reports")
        assert not inspect(engine).has_table("learning_actuals")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT status FROM design_audits WHERE id = :audit"), identifiers) == "running"
            assert connection.scalar(text("SELECT summary FROM design_versions WHERE id = :version"), identifiers) == "Existing version"
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        with test_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
