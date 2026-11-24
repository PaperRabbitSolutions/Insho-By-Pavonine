from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db import database
from conftest import test_engine


def test_production_migration_round_trip_preserves_phase9(monkeypatch):
    schema = "phase10_" + uuid4().hex
    with test_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(test_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(database, "engine", engine)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))

    try:
        command.upgrade(config, "02e73da81bf4")
        with engine.begin() as connection:
            project = connection.execute(text("""
                WITH org AS (
                    INSERT INTO organizations (id, name, slug) VALUES (gen_random_uuid(), 'Migration test', 'migration-test')
                    RETURNING id
                ), workspace AS (
                    INSERT INTO workspaces (id, organization_id, name, slug)
                    SELECT gen_random_uuid(), id, 'Default', 'default' FROM org RETURNING id, organization_id
                )
                INSERT INTO projects (id, organization_id, workspace_id, name, status)
                SELECT gen_random_uuid(), organization_id, id, 'Existing project', 'draft' FROM workspace RETURNING id
            """)).scalar_one()
            scene_id = connection.execute(text("""
                INSERT INTO spatial_scenes (project_id, name, width_m, depth_m, height_m)
                VALUES (:project, 'Existing scene', 10, 10, 5) RETURNING id
            """), {"project": project}).scalar_one()

        command.upgrade(config, "5b03a8d6e194")
        with engine.begin() as connection:
            record = connection.execute(text("""
                INSERT INTO production_records (scene_id, name, scene_revision)
                VALUES (:scene_id, 'Assembly plan', 1) RETURNING id
            """), {"scene_id": scene_id}).scalar_one()
            item = connection.execute(text("""
                INSERT INTO production_items (record_id, scene_id, name, kind)
                VALUES (:record, :scene_id, 'Deck', 'assembly') RETURNING id
            """), {"record": record, "scene_id": scene_id}).scalar_one()
            legacy = connection.execute(text("""
                INSERT INTO production_specifications (item_id, name, content)
                VALUES (:item, 'Legacy text', 'Part 1 detail') RETURNING id
            """), {"item": item}).scalar_one()
        command.upgrade(config, "6c28f1a4d705")
        with engine.begin() as connection:
            specification = connection.execute(text("""
                SELECT content, technical, version FROM production_specifications WHERE id = :id
            """), {"id": legacy}).one()
            assert tuple(specification) == ("Part 1 detail", None, 1)
            connection.execute(text("""
                INSERT INTO production_quantities (record_id, item_id, target, target_id, method, version, engine_version,
                    scene_revision, source_fingerprint, source, formula, parameters, inputs, outputs, value, unit, status, errors)
                VALUES (:record, :item, 'scene', :scene, 'plan_area', 1, 1, 1, 'fingerprint', '{}', 'width * depth',
                    '{}', '{}', '{}', 100, 'm2', 'calculated', '[]')
            """), {"record": record, "item": item, "scene": scene_id})
            usage = connection.execute(text("""
                INSERT INTO production_materials (record_id, name, description)
                VALUES (:record, 'Existing material', 'Original description') RETURNING id
            """), {"record": record}).scalar_one()
        command.upgrade(config, "7af04e25b1c9")
        with engine.begin() as connection:
            assignment = connection.execute(text("""
                SELECT name, description, definition_id, version, overrides FROM production_materials WHERE id = :id
            """), {"id": usage}).one()
            assert tuple(assignment) == ("Existing material", "Original description", None, 1, {})
            bom_id = connection.scalar(text("""
                INSERT INTO production_boms (record_id, version, record_version, scene_revision, currency, currency_precision)
                VALUES (:record, 1, 1, 1, 'INR', 2) RETURNING id
            """), {"record": record})
            line_id = connection.scalar(text("""
                INSERT INTO production_bom_lines (bom_id, line_key, position, name, origin, basis, category,
                    quantity, unit, unit_rate, total, inputs, cost, issues)
                VALUES (:bom, 'key', 0, 'Deck', 'manual', 'estimated', 'material', 2, 'each', 5, 10, '{}', '{}', '[]')
                RETURNING id
            """), {"bom": bom_id})
        command.upgrade(config, "8d31b6e2f4a7")
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO production_budgets (project_id, scope_key, version, currency, currency_precision,
                    amount, allocations, sources, costs)
                VALUES (:project, 'project', 1, 'INR', 2, 20, '{}', '{}', '{}')
            """), {"project": project})
            connection.execute(text("""
                INSERT INTO production_procurements (project_id, bom_line_id, status, version, requirement, selection, audit)
                VALUES (:project, :line, 'requested', 1, '{}', '{}', '[]')
            """), {"project": project, "line": line_id})
            connection.execute(text("UPDATE production_records SET status = 'approved', approved_at = now() WHERE id = :id"),
                               {"id": record})
        command.upgrade(config, "head")
        command.check(config)
        with engine.begin() as connection:
            assert tuple(connection.execute(text(
                "SELECT status, version, supersedes_id FROM production_records WHERE id = :id"
            ), {"id": record}).one()) == ("approved", 1, None)
            assert connection.scalar(text("SELECT count(*) FROM production_workflow_revisions")) == 0
            connection.execute(text("""
                INSERT INTO production_workflow_revisions (record_id, version, status, tasks)
                VALUES (:id, 1, 'design_approved', '[]')
            """), {"id": record})
        command.downgrade(config, "8d31b6e2f4a7")
        assert not inspect(engine).has_table("production_workflow_revisions")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT status FROM production_records WHERE id = :id"), {"id": record}) == "approved"
            assert connection.scalar(text("SELECT amount FROM production_budgets")) == 20
            assert connection.scalar(text("SELECT status FROM production_procurements")) == "requested"
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "7af04e25b1c9")
        assert not inspect(engine).has_table("production_procurements")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT total FROM production_bom_lines WHERE id = :id"),
                                     {"id": line_id}) == 10
        command.downgrade(config, "6c28f1a4d705")
        assert not inspect(engine).has_table("production_boms")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM production_quantities")) == 1
            assert connection.scalar(text("SELECT name FROM production_materials WHERE id = :id"),
                                     {"id": usage}) == "Existing material"
        command.downgrade(config, "5b03a8d6e194")
        assert not inspect(engine).has_table("production_quantities")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT content FROM production_specifications WHERE id = :id"),
                                     {"id": legacy}) == "Part 1 detail"
        command.downgrade(config, "02e73da81bf4")
        assert not any(table.startswith("production_") for table in inspect(engine).get_table_names())
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            scene_name = connection.scalar(text("SELECT name FROM spatial_scenes WHERE id = :id"), {"id": scene_id})
            assert scene_name == "Existing scene"
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(config).get_current_head()
    finally:
        engine.dispose()
        with test_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
