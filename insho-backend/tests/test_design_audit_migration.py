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


def test_audit_migration_retains_design_versions_and_protects_final_history(monkeypatch):
    schema = "audits_" + uuid4().hex
    with test_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(test_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(database, "engine", engine)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    previous = "921d7c306abe"
    identifiers = {field: uuid4() for field in ("snapshot", "alternate", "design", "audit", "request")}
    try:
        command.upgrade(config, previous)
        with engine.begin() as connection:
            identifiers["project"], identifiers["org"] = connection.execute(text("""
                WITH org AS (
                    INSERT INTO organizations (id, name, slug) VALUES (gen_random_uuid(), 'Audit fixture', 'audit-fixture') RETURNING id
                ), workspace AS (
                    INSERT INTO workspaces (id, organization_id, name, slug)
                    SELECT gen_random_uuid(), id, 'Fixture', 'fixture' FROM org RETURNING id, organization_id
                )
                INSERT INTO projects (id, organization_id, workspace_id, name, status)
                SELECT gen_random_uuid(), organization_id, id, 'Existing project', 'draft' FROM workspace RETURNING id, organization_id
            """)).one()
            connection.execute(text("""
                INSERT INTO design_snapshots (id, project_id, scope_key, state)
                VALUES (:snapshot, :project, 'project', jsonb_build_object('title', 'Existing snapshot')),
                       (:alternate, :project, 'project', '{}')
            """), identifiers)
            connection.execute(text("""
                INSERT INTO design_versions (id, project_id, scope_key, snapshot_id, number, summary, reason)
                VALUES (:design, :project, 'project', :snapshot, 1, 'Existing design', 'Keep the retained version')
            """), identifiers)
        command.upgrade(config, "head")
        command.check(config)
        insert = text("""
            INSERT INTO design_audits (id, organization_id, project_id, design_version_id, snapshot_id, request_id, status,
                                       configuration_version, options, input_fingerprint, results, warnings, retryable, evidence_sources)
            VALUES (:audit, :org, :project, :design, :snapshot, :request, 'running', '28.1-v1', '{}', repeat('0', 64), '[]', '[]', false, '[]')
        """)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert, {**identifiers, "snapshot": identifiers["alternate"]})
        with engine.begin() as connection:
            connection.execute(insert, identifiers)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE design_audits SET snapshot_id = :alternate WHERE id = :audit"), identifiers)
        with engine.begin() as connection:
            connection.execute(text("UPDATE design_audits SET status = 'failed', completed_at = now() WHERE id = :audit"), identifiers)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE design_audits SET warnings = jsonb_build_array('overwrite') WHERE id = :audit"), identifiers)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("DELETE FROM design_audits WHERE id = :audit"), identifiers)
        command.downgrade(config, previous)
        assert not inspect(engine).has_table("design_audits")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT summary FROM design_versions WHERE id = :design"), identifiers) == "Existing design"
            assert connection.scalar(text("SELECT state ->> 'title' FROM design_snapshots WHERE id = :snapshot"), identifiers) == "Existing snapshot"
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        with test_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
