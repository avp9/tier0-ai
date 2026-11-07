"""answer_feedback: an employee's thumbs up or down on an answer

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE answer_feedback (
            trace_id        uuid PRIMARY KEY,
            requester_email text NOT NULL,
            score           smallint NOT NULL,
            comment         text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT answer_feedback_score_valid CHECK (score IN (0, 1)),
            CONSTRAINT answer_feedback_email_lowercase
                CHECK (requester_email = lower(requester_email))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_answer_feedback_requester "
        "ON answer_feedback (requester_email, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE answer_feedback")
