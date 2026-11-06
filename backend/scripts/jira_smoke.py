"""Check the Jira adapter against your real Jira. Read-only unless --create is given.

  JIRA_SERVER=https://you.atlassian.net JIRA_USERNAME=you@x.com JIRA_API_TOKEN=... \
  APP_ENV=staging uv run python scripts/jira_smoke.py alice@buzztech.example
  ... --create    # also creates ONE clearly labelled test ticket, then reads it back

Tickets are scoped by the APP_ENV label (tier0-env:<APP_ENV>); use staging, not prod.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

from tier0.tickets.config import ConfigError, JiraConfig
from tier0.tickets.errors import TicketError
from tier0.tickets.jira_adapter import JiraAdapter
from tier0.tickets.models import TicketDraft


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "email", help="requester email to look up (or to create the test ticket for)"
    )
    parser.add_argument("--create", action="store_true", help="create one test ticket")
    args = parser.parse_args()

    try:
        config = JiraConfig.from_env(os.environ)
    except ConfigError as error:
        sys.exit(str(error))
    adapter = JiraAdapter(config)
    print(f"Project {config.project_key}, environment label {config.env_label}")

    try:
        if args.create:
            submission_id = str(uuid.uuid4())
            created = adapter.create_ticket(
                TicketDraft(
                    requester_email=args.email,
                    summary="Tier0 smoke test - safe to close",
                    description="Created by scripts/jira_smoke.py to verify the integration.",
                    category="IT",
                    priority="Low",
                    submission_id=submission_id,
                )
            )
            print(f"Created {created.key}: {created.url}")
            again = adapter.find_by_submission_id(submission_id, args.email)
            print(f"Found by submission id: {again.key if again else 'NOT FOUND'}")
        tickets = adapter.find_tickets(args.email)
        print(f"{len(tickets)} ticket(s) for {args.email}:")
        for ticket in tickets:
            print(f"  {ticket.key}  [{ticket.status}]  {ticket.title}")
        if tickets:
            details = adapter.get_ticket(tickets[0].key, args.email)
            print(f"{tickets[0].key} has {len(details.comments)} visible comment(s)")
    except TicketError as error:
        sys.exit(f"{type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
