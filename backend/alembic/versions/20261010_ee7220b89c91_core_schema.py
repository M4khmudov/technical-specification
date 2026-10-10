"""core schema

Revision ID: ee7220b89c91
Revises:
Create Date: 2026-10-10 16:43:11.026917
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ee7220b89c91"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEQUENCES = ("batch_number_seq", "job_number_seq")


def upgrade() -> None:
    # Автономера партий и заданий (autogenerate последовательности не создаёт)
    for name in SEQUENCES:
        op.execute(sa.schema.CreateSequence(sa.Sequence(name)))
    op.create_table(
        "materials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_materials")),
        sa.UniqueConstraint("code", name=op.f("uq_materials_code")),
    )
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipes")),
        sa.UniqueConstraint("name", name=op.f("uq_recipes_name")),
    )
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("name", name=op.f("uq_suppliers_name")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "storekeeper",
                "technologist",
                "manager",
                name="user_role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("login", name=op.f("uq_users_login")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("user_full_name", sa.String(length=255), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_audit_log_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"], unique=False)
    op.create_table(
        "production_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "number",
            sa.Integer(),
            server_default=sa.text("nextval('job_number_seq')"),
            nullable=False,
        ),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("planned_on", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "planned",
                "in_progress",
                "completed",
                "marked",
                "cancelled",
                name="job_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_production_jobs_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_production_jobs_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"], name=op.f("fk_production_jobs_recipe_id_recipes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_production_jobs")),
        sa.UniqueConstraint("number", name=op.f("uq_production_jobs_number")),
    )
    op.create_index(
        op.f("ix_production_jobs_planned_on"), "production_jobs", ["planned_on"], unique=False
    )
    op.create_index(op.f("ix_production_jobs_status"), "production_jobs", ["status"], unique=False)
    op.create_table(
        "receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_receipts_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"], name=op.f("fk_receipts_supplier_id_suppliers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipts")),
    )
    op.create_table(
        "recipe_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("qty_per_unit", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.CheckConstraint("qty_per_unit > 0", name=op.f("ck_recipe_items_qty_positive")),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], name=op.f("fk_recipe_items_material_id_materials")
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["recipes.id"],
            name=op.f("fk_recipe_items_recipe_id_recipes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipe_items")),
        sa.UniqueConstraint("recipe_id", "material_id", name=op.f("uq_recipe_items_recipe_id")),
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "number",
            sa.Integer(),
            server_default=sa.text("nextval('batch_number_seq')"),
            nullable=False,
        ),
        sa.Column("receipt_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("remaining_qty", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column(
            "reserved_qty", sa.Numeric(precision=14, scale=3), server_default="0", nullable=False
        ),
        sa.Column("price", sa.Numeric(precision=16, scale=6), nullable=False),
        sa.Column(
            "currency",
            sa.Enum(
                "RUB",
                "USD",
                "EUR",
                name="currency",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("exchange_rate", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("unit_cost_rub", sa.Numeric(precision=16, scale=6), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                "rejected",
                name="batch_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("exchange_rate > 0", name=op.f("ck_batches_rate_positive")),
        sa.CheckConstraint("price >= 0", name=op.f("ck_batches_price_non_negative")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_batches_quantity_positive")),
        sa.CheckConstraint("remaining_qty >= 0", name=op.f("ck_batches_remaining_non_negative")),
        sa.CheckConstraint(
            "reserved_qty <= remaining_qty", name=op.f("ck_batches_reserved_within_remaining")
        ),
        sa.CheckConstraint("reserved_qty >= 0", name=op.f("ck_batches_reserved_non_negative")),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], name=op.f("fk_batches_material_id_materials")
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["receipts.id"], name=op.f("fk_batches_receipt_id_receipts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batches")),
        sa.UniqueConstraint("number", name=op.f("uq_batches_number")),
    )
    op.create_index(
        "ix_batches_fifo", "batches", ["material_id", "status", "received_on", "id"], unique=False
    )
    op.create_table(
        "job_materials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("required_qty", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("actual_qty", sa.Numeric(precision=14, scale=3), nullable=True),
        sa.CheckConstraint(
            "actual_qty IS NULL OR actual_qty >= 0",
            name=op.f("ck_job_materials_actual_non_negative"),
        ),
        sa.CheckConstraint("required_qty > 0", name=op.f("ck_job_materials_required_positive")),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["production_jobs.id"],
            name=op.f("fk_job_materials_job_id_production_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], name=op.f("fk_job_materials_material_id_materials")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_materials")),
        sa.UniqueConstraint("job_id", "material_id", name=op.f("uq_job_materials_job_id")),
    )
    op.create_table(
        "marking_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sent",
                "failed",
                name="marking_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempts_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["production_jobs.id"],
            name=op.f("fk_marking_reports_job_id_production_jobs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marking_reports")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_marking_reports_idempotency_key")),
        sa.UniqueConstraint("job_id", name=op.f("uq_marking_reports_job_id")),
    )
    op.create_table(
        "batch_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "receipt",
                "reserve",
                "release",
                "consume",
                name="movement_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_batch_movements_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batches.id"], name=op.f("fk_batch_movements_batch_id_batches")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["production_jobs.id"],
            name=op.f("fk_batch_movements_job_id_production_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_batch_movements_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batch_movements")),
    )
    op.create_index(
        op.f("ix_batch_movements_batch_id"), "batch_movements", ["batch_id"], unique=False
    )
    op.create_index(op.f("ix_batch_movements_job_id"), "batch_movements", ["job_id"], unique=False)
    op.create_table(
        "marking_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["marking_reports.id"],
            name=op.f("fk_marking_attempts_report_id_marking_reports"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marking_attempts")),
    )
    op.create_index(
        op.f("ix_marking_attempts_report_id"), "marking_attempts", ["report_id"], unique=False
    )
    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_reservations_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batches.id"], name=op.f("fk_reservations_batch_id_batches")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["production_jobs.id"],
            name=op.f("fk_reservations_job_id_production_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], name=op.f("fk_reservations_material_id_materials")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservations")),
        sa.UniqueConstraint("job_id", "batch_id", name=op.f("uq_reservations_job_id")),
    )
    op.create_index(op.f("ix_reservations_batch_id"), "reservations", ["batch_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reservations_batch_id"), table_name="reservations")
    op.drop_table("reservations")
    op.drop_index(op.f("ix_marking_attempts_report_id"), table_name="marking_attempts")
    op.drop_table("marking_attempts")
    op.drop_index(op.f("ix_batch_movements_job_id"), table_name="batch_movements")
    op.drop_index(op.f("ix_batch_movements_batch_id"), table_name="batch_movements")
    op.drop_table("batch_movements")
    op.drop_table("marking_reports")
    op.drop_table("job_materials")
    op.drop_index("ix_batches_fifo", table_name="batches")
    op.drop_table("batches")
    op.drop_table("recipe_items")
    op.drop_table("receipts")
    op.drop_index(op.f("ix_production_jobs_status"), table_name="production_jobs")
    op.drop_index(op.f("ix_production_jobs_planned_on"), table_name="production_jobs")
    op.drop_table("production_jobs")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("users")
    op.drop_table("suppliers")
    op.drop_table("recipes")
    op.drop_table("materials")
    for name in SEQUENCES:
        op.execute(sa.schema.DropSequence(sa.Sequence(name)))
