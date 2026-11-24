"""Enforce exact Phase 11 presentation and approval relationships."""

from alembic import op


revision = "e35f18c42a79"
down_revision = "d914b27f803c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_foreign_key(
        "fk_presentations_event_project", "presentations", "events",
        ["event_id", "project_id"], ["id", "project_id"], ondelete="RESTRICT",
    )
    op.drop_constraint("fk_presentations_event", "presentations", type_="foreignkey")
    op.execute("""
        CREATE FUNCTION insho_presentation_scope_valid(sheet presentations) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT (sheet.event_id IS NULL OR EXISTS (
                        SELECT 1 FROM events e JOIN projects p ON p.id = sheet.project_id
                        WHERE e.id = sheet.event_id AND e.project_id = p.id AND e.organization_id = p.organization_id
                   )) AND (sheet.alternative_of_id IS NULL OR EXISTS (
                        SELECT 1 FROM presentations parent
                        WHERE parent.id = sheet.alternative_of_id AND parent.project_id = sheet.project_id
                        AND parent.event_id IS NOT DISTINCT FROM sheet.event_id AND parent.alternative_of_id IS NULL
                   ))
        $$
    """)
    op.execute("""
        CREATE FUNCTION insho_review_matches_snapshot(binding design_version_reviews) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM design_versions v
                JOIN design_snapshots s ON s.id = v.snapshot_id
                JOIN presentations p ON p.id = binding.presentation_id
                WHERE v.id = binding.version_id AND v.project_id = binding.project_id AND p.project_id = v.project_id
                  AND p.event_id IS NOT DISTINCT FROM v.event_id
                  AND s.state->'presentation'->>'id' = p.id::text
                  AND s.state->'presentation'->>'revision' = binding.presentation_revision::text
            )
        $$
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM presentations p WHERE NOT insho_presentation_scope_valid(p)) THEN
                RAISE EXCEPTION 'Existing presentation scope does not match its event or parent' USING ERRCODE = '23514';
            END IF;
            IF EXISTS (SELECT 1 FROM design_version_reviews r WHERE NOT insho_review_matches_snapshot(r)) THEN
                RAISE EXCEPTION 'Existing review binding does not match its design snapshot' USING ERRCODE = '23514';
            END IF;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION insho_guard_presentation_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' AND NOT insho_presentation_scope_valid(NEW) THEN
                RAISE EXCEPTION 'The presentation does not match its event or parent scope' USING ERRCODE = '23514';
            ELSIF TG_OP = 'UPDATE' AND
                (NEW.id, NEW.project_id, NEW.event_id, NEW.alternative_of_id, NEW.created_at)
                    IS DISTINCT FROM (OLD.id, OLD.project_id, OLD.event_id, OLD.alternative_of_id, OLD.created_at) THEN
                RAISE EXCEPTION 'Presentation scope is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION insho_guard_checkpoint_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.id, NEW.presentation_id, NEW.revision, NEW.created_at)
                IS DISTINCT FROM (OLD.id, OLD.presentation_id, OLD.revision, OLD.created_at) THEN
                RAISE EXCEPTION 'Checkpoint identity and revision are immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION insho_guard_design_review_binding() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT insho_review_matches_snapshot(NEW) THEN
                RAISE EXCEPTION 'The review binding must match the design snapshot and event' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    for trigger, operation, table in (
        ("insho_guard_presentation_scope", "INSERT OR UPDATE", "presentations"),
        ("insho_guard_checkpoint_identity", "UPDATE", "presentation_checkpoints"),
        ("insho_guard_design_review_binding", "INSERT", "design_version_reviews"),
    ):
        op.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {table} FOR EACH ROW EXECUTE FUNCTION {trigger}()")


def downgrade():
    for name, table in (
        ("insho_guard_design_review_binding", "design_version_reviews"),
        ("insho_guard_checkpoint_identity", "presentation_checkpoints"),
        ("insho_guard_presentation_scope", "presentations"),
    ):
        op.execute(f"DROP TRIGGER {name} ON {table}")
        op.execute(f"DROP FUNCTION {name}()")
    op.execute("DROP FUNCTION insho_review_matches_snapshot(design_version_reviews)")
    op.execute("DROP FUNCTION insho_presentation_scope_valid(presentations)")
    op.create_foreign_key("fk_presentations_event", "presentations", "events", ["event_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint("fk_presentations_event_project", "presentations", type_="foreignkey")
