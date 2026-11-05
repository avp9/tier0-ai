# 1. The signed-in Employee's email is the Requester Email

**Status:** Accepted (2026-11-05)

## Context

The specification's baseline identity mechanism is email collection: the agent asks the Employee for their email when it needs one (FR-STATUS-01, AT-04, AT-09). Tier0 is hosted on a public Azure URL, so under that baseline anyone who types another person's email can read that person's Tickets. The spec itself flags this (FR-STATUS-06, §10 item 10).

Alternatives considered:

- **Email only, documented risk** — matches the spec literally, but leaves Ticket data exposed on a public URL.
- **Microsoft Entra ID sign-in** — strongest, but adds tenant setup and app registration that a capstone grader can't easily use.
- **Demo accounts with passwords** — chosen.

## Decision

Employees sign in with one of a small set of demo accounts (email + password). The session's signed-in email is the Requester Email for every Ticket created and every status lookup. The agent never asks for an email and never accepts one typed into the chat as an identity. Demo passwords are supplied via secrets, stored hashed, and never committed.

## Consequences

- Ticket lookups are restricted to the signed-in Employee's own Tickets.
- AT-04 and AT-09 are reinterpreted: "email is taken from the session" rather than "agent asks for email". This deviation must be stated in `docs/design-decisions.md` for graders.
- Graders need demo credentials shared privately.
- Moving to Entra ID later only changes where the signed-in email comes from; the rest of the workflow is unaffected.
