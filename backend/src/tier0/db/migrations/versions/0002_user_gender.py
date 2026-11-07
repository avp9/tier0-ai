"""users.gender, which picks the avatar shown in the header

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN gender text,
            ADD CONSTRAINT users_gender_valid CHECK (gender IN ('male', 'female'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN gender")
