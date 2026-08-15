"""Add persistent local client remarks."""

import sqlalchemy as sa
from alembic import op

revision = "0002_client_local_remark"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("clients")}
    if "local_remark" not in columns:
        op.add_column(
            "clients",
            sa.Column("local_remark", sa.String(length=255), nullable=False, server_default=""),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("clients")}
    if "local_remark" in columns:
        op.drop_column("clients", "local_remark")
