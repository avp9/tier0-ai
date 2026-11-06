"""users and ticket_submissions

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email         text NOT NULL,
            display_name  text NOT NULL,
            password_hash text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT users_email_unique UNIQUE (email),
            CONSTRAINT users_email_lowercase CHECK (email = lower(email))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ticket_submissions (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            thread_id       text NOT NULL,
            requester_email text NOT NULL,
            payload_hash    text NOT NULL,
            status          text NOT NULL DEFAULT 'pending',
            ticket_key      text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ticket_submissions_status_valid
                CHECK (status IN ('pending', 'submitted', 'failed', 'unknown', 'cancelled')),
            CONSTRAINT ticket_submissions_key_iff_submitted
                CHECK ((status = 'submitted') = (ticket_key IS NOT NULL)),
            CONSTRAINT ticket_submissions_email_lowercase
                CHECK (requester_email = lower(requester_email))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_ticket_submissions_requester ON ticket_submissions (requester_email)"
    )
    # One live submission per conversation and ticket content: a retry reuses it instead of
    # creating a second ticket. Cancelled or failed submissions do not block a fresh one.
    op.execute(
        """
        CREATE UNIQUE INDEX ux_ticket_submissions_live
            ON ticket_submissions (thread_id, payload_hash)
            WHERE status IN ('pending', 'submitted', 'unknown')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE ticket_submissions")
    op.execute("DROP TABLE users")
