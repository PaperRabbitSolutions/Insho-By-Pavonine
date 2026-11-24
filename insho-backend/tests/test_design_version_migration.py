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


def test_part2_migration_preserves_legacy_memory_and_enforces_history(monkeypatch):
    schema = "versions_" + uuid4().hex
    with test_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(test_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(database, "engine", engine)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    previous = "73fd6a4ce211"
    try:
        command.upgrade(config, previous)
        with engine.begin() as connection:
            projects = connection.execute(text("""
                WITH org AS (
                    INSERT INTO organizations (id, name, slug) VALUES (gen_random_uuid(), 'Fixture', 'fixture') RETURNING id
                ), workspace AS (
                    INSERT INTO workspaces (id, organization_id, name, slug)
                    SELECT gen_random_uuid(), id, 'Fixture', 'fixture' FROM org RETURNING id, organization_id
                )
                INSERT INTO projects (id, organization_id, workspace_id, name, status)
                SELECT gen_random_uuid(), organization_id, id, name, 'draft' FROM workspace
                CROSS JOIN (VALUES ('First'), ('Second')) AS projects(name) RETURNING id
            """)).scalars().all()
            event_id = connection.scalar(text("""
                INSERT INTO events (id, organization_id, project_id, name)
                SELECT gen_random_uuid(), organization_id, id, 'Existing event' FROM projects WHERE id = :id RETURNING id
            """), {"id": projects[0]})
            item = connection.scalar(text("""
                INSERT INTO project_memory_items (project_id, event_id, kind, title, content, source_reference)
                VALUES (:project, :event, 'preference', 'Existing preference', '{"color":"indigo"}', 'Signed brief') RETURNING id
            """), {"project": projects[0], "event": event_id})
        command.upgrade(config, "head")
        command.check(config)
        design_id, snapshot_id, actor_id = uuid4(), uuid4(), uuid4()
        with engine.begin() as connection:
            legacy = connection.execute(text("""
                SELECT item.category, history.state, history.action FROM project_memory_items item
                JOIN project_memory_revisions history ON history.item_id = item.id WHERE item.id = :id
            """), {"id": item}).one()
            assert legacy.category == "client_preferences" and legacy.action == "baseline"
            assert legacy.state["content"] == {"color": "indigo"} and legacy.state["source_reference"] == "Signed brief"
            connection.execute(text("""
                INSERT INTO users (id, full_name, email, password_hash, is_active)
                VALUES (:id, 'Fixture', 'fixture@example.test', 'fixture', true)
            """), {"id": actor_id})
            connection.execute(text("INSERT INTO design_snapshots (id, project_id, scope_key, state) VALUES (:snap, :project, :scope, '{}')"),
                               {"snap": snapshot_id, "project": projects[0], "scope": str(event_id)})
            connection.execute(text("""
                INSERT INTO design_versions (id, project_id, event_id, scope_key, snapshot_id, number, summary, reason, created_by_user_id)
                VALUES (:id, :project, :event, :scope, :snapshot, 1, 'Captured', 'Test rollback', :actor)
            """), {"id": design_id, "project": projects[0], "event": event_id, "scope": str(event_id), "snapshot": snapshot_id,
                   "actor": actor_id})
        for statement, params in (
            ("UPDATE design_versions SET summary = 'Overwrite' WHERE id = :id", {"id": design_id}),
            ("UPDATE design_snapshots SET state = jsonb_build_object('changed', true) WHERE id = :id", {"id": snapshot_id}),
            ("UPDATE project_memory_revisions SET state = '{}' WHERE item_id = :id", {"id": item}),
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(statement), params)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO project_memory_items (project_id, kind, title, content, linked_version_id)
                VALUES (:project, 'fact', 'Cross-project link', jsonb_build_object('fixture', true), :version)
            """), {"project": projects[1], "version": design_id})
        with pytest.raises(IntegrityError), engine.begin() as connection:
            foreign_snapshot = uuid4()
            connection.execute(text("INSERT INTO design_snapshots (id, project_id, scope_key, state) VALUES (:id, :project, :scope, '{}')"),
                               {"id": foreign_snapshot, "project": projects[1], "scope": str(event_id)})
            connection.execute(text("""
                INSERT INTO design_versions (id, project_id, event_id, scope_key, snapshot_id, number, summary, reason)
                VALUES (:id, :project, :event, :scope, :snapshot, 1, 'Wrong event project', 'Constraint test')
            """), {"id": uuid4(), "project": projects[1], "event": event_id, "scope": str(event_id), "snapshot": foreign_snapshot})
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM design_snapshots WHERE id = :id"), {"id": foreign_snapshot}) == 0
            connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": actor_id})
            assert tuple(connection.execute(text("SELECT summary, created_by_user_id FROM design_versions WHERE id = :id"),
                                            {"id": design_id}).one()) == ("Captured", None)
        command.downgrade(config, previous)
        assert not inspect(engine).has_table("design_versions")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT content FROM project_memory_items WHERE id = :id"), {"id": item}) == {"color": "indigo"}
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        with test_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
