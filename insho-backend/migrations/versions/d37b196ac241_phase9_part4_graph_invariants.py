"""Add scene default layers and guard graph integrity at the database boundary."""

from alembic import op
import sqlalchemy as sa


revision = "d37b196ac241"
down_revision = "c481a7d02f64"
branch_labels = None
depends_on = None

PARENTS = (("spatial_objects", "parent_object_id"), ("spatial_zones", "parent_zone_id"))


def upgrade():
    op.add_column("spatial_layers", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("uq_spatial_layers_scene_default", "spatial_layers", ["scene_id"], unique=True,
                    postgresql_where=sa.text("is_default"))
    _install_graph_guards()


def _install_graph_guards():
    op.execute("""
        CREATE FUNCTION spatial_reject_parent_cycle() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            parent_id uuid := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
            has_cycle boolean;
        BEGIN
            -- Touch the scene row to serialize structural writers, including at repeatable read.
            EXECUTE format('UPDATE %I.spatial_scenes SET current_revision = current_revision WHERE id = $1',
                           TG_TABLE_SCHEMA) USING NEW.scene_id;
            EXECUTE format(
                'WITH RECURSIVE ancestors(id) AS (
                    SELECT $1::uuid
                    UNION
                    SELECT child.%1$I FROM %2$I.%3$I AS child
                    JOIN ancestors ON child.id = ancestors.id AND child.scene_id = $2
                    WHERE child.%1$I IS NOT NULL
                ) SELECT EXISTS (SELECT 1 FROM ancestors WHERE id = $3)',
                TG_ARGV[0], TG_TABLE_SCHEMA, TG_TABLE_NAME
            ) INTO has_cycle USING parent_id, NEW.scene_id, NEW.id;
            IF has_cycle THEN
                RAISE check_violation USING MESSAGE = 'The spatial hierarchy cannot contain a cycle.',
                    CONSTRAINT = 'ck_' || TG_TABLE_NAME || '_parent_acyclic';
            END IF;
            RETURN NULL;
        END $$;
    """)
    for table, parent in PARENTS:
        op.execute(f"""
            CREATE TRIGGER tr_{table}_parent_insert
            AFTER INSERT ON {table} FOR EACH ROW WHEN (NEW.{parent} IS NOT NULL)
            EXECUTE FUNCTION spatial_reject_parent_cycle('{parent}');
        """)
        op.execute(f"""
            CREATE TRIGGER tr_{table}_parent_update
            AFTER UPDATE OF {parent} ON {table} FOR EACH ROW
            WHEN (NEW.{parent} IS NOT NULL AND OLD.{parent} IS DISTINCT FROM NEW.{parent})
            EXECUTE FUNCTION spatial_reject_parent_cycle('{parent}');
        """)
        # Check records installed before the triggers. UNION terminates even if a cycle exists.
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    WITH RECURSIVE ancestry(scene_id, source, id) AS (
                        SELECT scene_id, id, {parent} FROM {table} WHERE {parent} IS NOT NULL
                        UNION
                        SELECT node.scene_id, ancestry.source, node.{parent} FROM {table} AS node
                        JOIN ancestry ON node.id = ancestry.id AND node.scene_id = ancestry.scene_id
                        WHERE node.{parent} IS NOT NULL
                    )
                    SELECT 1 FROM ancestry WHERE source = id
                ) THEN
                    RAISE EXCEPTION 'Cannot upgrade a spatial hierarchy containing a cycle';
                END IF;
            END $$;
        """)

    op.execute("""
        CREATE FUNCTION spatial_protect_locked_scene() RETURNS trigger
        LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM spatial_objects WHERE scene_id = OLD.id AND is_locked)
                OR EXISTS (SELECT 1 FROM spatial_zones WHERE scene_id = OLD.id AND is_locked)
                OR EXISTS (SELECT 1 FROM spatial_layers WHERE scene_id = OLD.id AND is_locked) THEN
                RAISE check_violation USING MESSAGE = 'The operation affects a locked scene graph entity.',
                    CONSTRAINT = 'ck_spatial_scene_delete_locked';
            END IF;
            RETURN OLD;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER tr_spatial_scene_protect_locked
        BEFORE DELETE ON spatial_scenes FOR EACH ROW
        EXECUTE FUNCTION spatial_protect_locked_scene();
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM spatial_layers WHERE is_default) THEN
            RAISE EXCEPTION 'Clear scene default layers before downgrading';
          END IF;
        END $$;
    """)
    op.execute("DROP TRIGGER tr_spatial_scene_protect_locked ON spatial_scenes")
    op.execute("DROP FUNCTION spatial_protect_locked_scene()")
    for table, _ in PARENTS:
        op.execute(f"DROP TRIGGER tr_{table}_parent_update ON {table}")
        op.execute(f"DROP TRIGGER tr_{table}_parent_insert ON {table}")
    op.execute("DROP FUNCTION spatial_reject_parent_cycle()")
    op.drop_index("uq_spatial_layers_scene_default", table_name="spatial_layers")
    op.drop_column("spatial_layers", "is_default")
