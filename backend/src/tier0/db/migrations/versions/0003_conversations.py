"""conversations: the list of each employee's chats shown in the history sidebar

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations (
            thread_id       text PRIMARY KEY,
            requester_email text NOT NULL,
            title           text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT conversations_email_lowercase
                CHECK (requester_email = lower(requester_email))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_conversations_requester_updated "
        "ON conversations (requester_email, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE conversations")
