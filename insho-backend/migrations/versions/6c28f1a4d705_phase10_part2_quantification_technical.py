"""Add quantity calculation history and extend production specifications."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "6c28f1a4d705"
down_revision = "5b03a8d6e194"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "production_quantities",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("target", sa.String(8), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("variant", sa.String(4), server_default="", nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("engine_version", sa.Integer(), nullable=False),
        sa.Column("scene_revision", sa.Integer(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("source", JSONB(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("parameters", JSONB(), nullable=False),
        sa.Column("inputs", JSONB(), nullable=False),
        sa.Column("outputs", JSONB(), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=True),
        sa.Column("unit", sa.String(4), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("errors", JSONB(), nullable=False),
        sa.Column("calculated_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id", "record_id"], ["production_items.id", "production_items.record_id"],
            name="fk_production_quantities_item_record", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("record_id", "target", "target_id", "method", "variant", "version",
                            name="uq_production_quantities_source_version"),
        sa.CheckConstraint("version > 0 AND engine_version > 0 AND scene_revision > 0",
                           name="ck_production_quantities_versions"),
        sa.CheckConstraint("target IN ('scene', 'object', 'zone')", name="ck_production_quantities_target"),
        sa.CheckConstraint(
            "(method IN ('plan_area', 'surface_area', 'led_surface') AND unit = 'm2') OR "
            "(method = 'linear' AND unit = 'm') OR (method = 'solid_volume' AND unit = 'm3') OR "
            "(method IN ('object_count', 'floral_stems') AND unit = 'each')",
            name="ck_production_quantities_method_unit",
        ),
        sa.CheckConstraint(
            "value IS NULL OR (value > 0 AND value <> 'NaN'::numeric AND (unit <> 'each' OR value = trunc(value)))",
            name="ck_production_quantities_value",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(errors) = 'array' AND ((status = 'calculated' AND value IS NOT NULL AND jsonb_array_length(errors) = 0) "
            "OR (status = 'invalid' AND value IS NULL AND jsonb_array_length(errors) > 0))",
            name="ck_production_quantities_status",
        ),
    )
    op.create_index("ix_production_quantities_item", "production_quantities", ["item_id"])
    op.create_index("ix_production_quantities_record_created", "production_quantities", ["record_id", "calculated_at", "id"])
    op.add_column("production_specifications", sa.Column("technical", JSONB(none_as_null=True)))
    op.add_column("production_specifications", sa.Column("supersedes_id", sa.Uuid()))
    op.add_column("production_specifications", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.add_column("production_specifications", sa.Column("approved_by_user_id", sa.Uuid()))
    op.create_unique_constraint("uq_production_specs_supersedes", "production_specifications", ["supersedes_id"])
    op.create_foreign_key(
        "fk_production_specs_predecessor_item", "production_specifications", "production_specifications",
        ["supersedes_id", "item_id"], ["id", "item_id"], initially="DEFERRED", deferrable=True,
    )
    op.create_foreign_key(
        "fk_production_specs_approved_by", "production_specifications", "users",
        ["approved_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint("ck_production_specs_predecessor", "production_specifications", "supersedes_id <> id")
    op.create_check_constraint("ck_production_specs_technical", "production_specifications",
                               "technical IS NULL OR jsonb_typeof(technical) = 'object'")
    op.create_check_constraint("ck_production_specs_approval", "production_specifications",
                               "approved_by_user_id IS NULL OR approved_at IS NOT NULL")


def downgrade():
    op.drop_constraint("ck_production_specs_approval", "production_specifications", type_="check")
    op.drop_constraint("ck_production_specs_technical", "production_specifications", type_="check")
    op.drop_constraint("ck_production_specs_predecessor", "production_specifications", type_="check")
    op.drop_constraint("fk_production_specs_approved_by", "production_specifications", type_="foreignkey")
    op.drop_constraint("fk_production_specs_predecessor_item", "production_specifications", type_="foreignkey")
    op.drop_constraint("uq_production_specs_supersedes", "production_specifications", type_="unique")
    op.drop_column("production_specifications", "approved_by_user_id")
    op.drop_column("production_specifications", "approved_at")
    op.drop_column("production_specifications", "supersedes_id")
    op.drop_column("production_specifications", "technical")
    op.drop_table("production_quantities")
