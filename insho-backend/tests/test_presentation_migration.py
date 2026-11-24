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


def test_presentation_migration_preserves_prior_data_and_scopes_checkpoint_revisions(monkeypatch):
    schema = "present_" + uuid4().hex
    with test_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(test_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(database, "engine", engine)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    previous, head = "4b9d5a732e10", "head"
    try:
        command.upgrade(config, previous)
        with engine.begin() as connection:
            project, event = connection.execute(text("""
                WITH org AS (
                    INSERT INTO organizations (id, name, slug) VALUES (gen_random_uuid(), 'Existing org', 'existing-org')
                    RETURNING id
                ), workspace AS (
                    INSERT INTO workspaces (id, organization_id, name, slug)
                    SELECT gen_random_uuid(), id, 'Existing workspace', 'existing-workspace' FROM org
                    RETURNING id, organization_id
                ), project AS (
                    INSERT INTO projects (id, organization_id, workspace_id, name, status)
                    SELECT gen_random_uuid(), organization_id, id, 'Existing project', 'draft' FROM workspace
                    RETURNING id, organization_id
                )
                INSERT INTO events (id, organization_id, project_id, name)
                SELECT gen_random_uuid(), organization_id, id, 'Existing event' FROM project
                RETURNING project_id, id
            """)).one()
            dna = connection.scalar(text("""
                INSERT INTO design_dna (id, project_id, palette, version)
                VALUES (gen_random_uuid(), :project, '{"primary":"indigo"}', 3) RETURNING id
            """), {"project": project})

        command.upgrade(config, head)
        command.check(config)
        values = {"present": uuid4(), "project": project, "event": event}
        with engine.begin() as connection:
            assert tuple(connection.execute(text("SELECT version, palette FROM design_dna WHERE id = :id"),
                                            {"id": dna}).one()) == (3, {"primary": "indigo"})
            connection.execute(text("INSERT INTO presentations (id, project_id, event_id) VALUES (:present, :project, :event)"), values)
            connection.execute(text("""
                INSERT INTO presentation_revisions (presentation_id, version, sheet, evidence)
                VALUES (:present, 1, '{"title":"Presentation"}', '{}')
            """), values)
            connection.execute(text("""
                INSERT INTO presentation_checkpoints (id, presentation_id, revision, title, status)
                VALUES (gen_random_uuid(), :present, 1, 'Review', 'draft')
            """), values)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO presentation_checkpoints (id, presentation_id, revision, title, status)
                VALUES (gen_random_uuid(), :present, 2, 'Wrong revision', 'draft')
            """), values)
        command.downgrade(config, previous)
        assert not inspect(engine).has_table("presentations")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT name FROM events WHERE id = :id"), {"id": event}) == "Existing event"
            assert connection.scalar(text("SELECT version FROM design_dna WHERE id = :id"), {"id": dna}) == 3
        command.upgrade(config, head)
        command.check(config)
    finally:
        engine.dispose()
        with test_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
